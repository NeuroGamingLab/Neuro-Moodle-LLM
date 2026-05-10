<?php
// Create a Quiz activity in a section, populated with multichoice questions.
//
// Idempotent: if a quiz with the same name already exists in the same
// section, reuses it (does NOT add duplicate questions on re-run; it
// returns the existing instance).
//
// Each question has: stem, 4 options (one correct), and optional
// general_feedback that we use to encode the synthetic must_cite (read
// later by the agentic feedback eval).

namespace local_neurollm\external;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->dirroot . '/course/modlib.php');
require_once($CFG->dirroot . '/mod/quiz/locallib.php');
require_once($CFG->dirroot . '/question/engine/lib.php');
require_once($CFG->dirroot . '/question/type/multichoice/questiontype.php');
require_once($CFG->libdir . '/questionlib.php');

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use stdClass;

class create_quiz_with_questions extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'course_id'   => new external_value(PARAM_INT, 'Target Moodle course id'),
            'section_num' => new external_value(PARAM_INT, 'Section number'),
            'name'        => new external_value(PARAM_TEXT, 'Quiz activity name'),
            'intro_html'  => new external_value(PARAM_RAW, 'Quiz description (HTML)', VALUE_DEFAULT, ''),
            'questions'   => new external_multiple_structure(
                new external_single_structure([
                    'stem'              => new external_value(PARAM_RAW, 'Question stem (HTML)'),
                    'options'           => new external_multiple_structure(
                        new external_value(PARAM_RAW, 'Answer option text (HTML)'),
                        '4 options recommended; first must_cite-related is correct'
                    ),
                    'correct_index'     => new external_value(PARAM_INT, '0-based index of the correct option'),
                    'must_cite'         => new external_value(PARAM_TEXT, 'Citation tag for downstream eval', VALUE_DEFAULT, ''),
                    'expected_topics'   => new external_multiple_structure(
                        new external_value(PARAM_TEXT, 'Expected-topic phrase'),
                        'Eval signal: topics that the agentic critique should cite',
                        VALUE_DEFAULT, []
                    ),
                ]),
                'List of multichoice question definitions'
            ),
        ]);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'cmid'         => new external_value(PARAM_INT, 'Course module id'),
            'quiz_id'      => new external_value(PARAM_INT, 'mdl_quiz.id'),
            'created'      => new external_value(PARAM_BOOL, 'True if newly created'),
            'question_ids' => new external_multiple_structure(new external_value(PARAM_INT, 'Question id')),
            'view_url'     => new external_value(PARAM_URL, 'Quiz view URL'),
            'eval_meta'    => new external_multiple_structure(
                new external_single_structure([
                    'question_id'     => new external_value(PARAM_INT),
                    'must_cite'       => new external_value(PARAM_TEXT),
                    'expected_topics' => new external_multiple_structure(new external_value(PARAM_TEXT)),
                ]),
                'Per-question synthetic ground truth, suitable for the agentic-feedback eval'
            ),
        ]);
    }

    public static function execute(int $courseid, int $sectionnum, string $name, string $introhtml = '', array $questions = []): array {
        global $DB, $CFG, $USER;

        $params = self::validate_parameters(self::execute_parameters(), [
            'course_id'   => $courseid,
            'section_num' => $sectionnum,
            'name'        => $name,
            'intro_html'  => $introhtml,
            'questions'   => $questions,
        ]);

        $course = get_course($params['course_id']);
        $coursectx = \context_course::instance($course->id);
        self::validate_context($coursectx);
        require_capability('moodle/course:manageactivities', $coursectx);
        require_capability('moodle/question:add', $coursectx);

        // Look for an existing quiz with the same name in this section.
        $existingcm = self::find_quiz_cm_in_section($course->id, (int) $params['section_num'], $params['name']);
        if ($existingcm) {
            $quiz = $DB->get_record('quiz', ['id' => $existingcm->instance], '*', MUST_EXIST);
            $existingqs = self::list_quiz_question_ids((int) $quiz->id);
            return [
                'cmid'         => (int) $existingcm->id,
                'quiz_id'      => (int) $quiz->id,
                'created'      => false,
                'question_ids' => $existingqs,
                'view_url'     => (new \moodle_url('/mod/quiz/view.php', ['id' => $existingcm->id]))->out(false),
                'eval_meta'    => self::collect_eval_meta($existingqs),
            ];
        }

        // Create the quiz instance + the course_module row + section linkage
        // directly. We avoid add_moduleinfo() because Moodle's quiz mod_form
        // postprocessing nulls empty optional strings (notably `password`),
        // which violates the column's NOT NULL constraint when called outside
        // a real form submission.
        $module = $DB->get_record('modules', ['name' => 'quiz'], '*', MUST_EXIST);

        $quizdef = new stdClass();
        $quizdef->course             = $course->id;
        $quizdef->name               = $params['name'];
        $quizdef->intro              = $params['intro_html'];
        $quizdef->introformat        = FORMAT_HTML;
        $quizdef->timeopen           = 0;
        $quizdef->timeclose          = 0;
        $quizdef->timelimit          = 0;
        $quizdef->preferredbehaviour = 'deferredfeedback';
        $quizdef->attempts           = 0;
        $quizdef->attemptonlast      = 0;
        $quizdef->grademethod        = 1; // QUIZ_GRADEHIGHEST
        $quizdef->decimalpoints      = 2;
        $quizdef->questiondecimalpoints = -1;
        $quizdef->reviewattempt          = 0x10010;
        $quizdef->reviewcorrectness      = 0x10010;
        $quizdef->reviewmarks            = 0x10010;
        $quizdef->reviewspecificfeedback = 0x10010;
        $quizdef->reviewgeneralfeedback  = 0x10010;
        $quizdef->reviewrightanswer      = 0x10010;
        $quizdef->reviewoverallfeedback  = 0x10010;
        $quizdef->reviewmaxmarks         = 0x10010;
        $quizdef->questionsperpage = 1;
        $quizdef->shuffleanswers   = 1;
        $quizdef->sumgrades        = 0;
        $quizdef->grade            = 10;
        $quizdef->password         = '';
        $quizdef->subnet           = '';
        $quizdef->browsersecurity  = '-';
        $quizdef->delay1           = 0;
        $quizdef->delay2           = 0;
        $quizdef->showuserpicture  = 0;
        $quizdef->showblocks       = 0;
        $quizdef->navmethod        = 'free';
        $quizdef->timecreated      = time();
        $quizdef->timemodified     = time();

        $quizdef->id = $DB->insert_record('quiz', $quizdef);
        $quiz = $DB->get_record('quiz', ['id' => $quizdef->id], '*', MUST_EXIST);

        // Hook the quiz into the course as a Course Module under the right section.
        $cmid = self::add_to_course($course, (int) $params['section_num'], $module->id, $quiz->id, $params['name']);
        $cm = $DB->get_record('course_modules', ['id' => $cmid], '*', MUST_EXIST);

        // Course-level question category (idempotent).
        $catid = self::ensure_course_question_category($course, $coursectx);

        // Save each question and add to the quiz.
        $createdqids = [];
        $evalmeta = [];
        foreach ($params['questions'] as $idx => $qdef) {
            $qid = self::create_multichoice_question(
                $catid,
                $course,
                (int) $idx,
                (string) $qdef['stem'],
                array_values((array) $qdef['options']),
                (int) $qdef['correct_index'],
                (string) ($qdef['must_cite'] ?? ''),
                (array) ($qdef['expected_topics'] ?? [])
            );
            \quiz_add_quiz_question($qid, $quiz, 0);
            $createdqids[] = (int) $qid;
            $evalmeta[] = [
                'question_id'     => (int) $qid,
                'must_cite'       => (string) ($qdef['must_cite'] ?? ''),
                'expected_topics' => array_values((array) ($qdef['expected_topics'] ?? [])),
            ];
        }

        // Recalculate sumgrades so the quiz has a non-zero max. Moodle 5
        // removed the legacy `quiz_update_sumgrades()`; the supported call is
        // `\mod_quiz\quiz_settings::create()->get_grade_calculator()->recompute_quiz_sumgrades()`.
        if (!empty($createdqids)) {
            $quizsettings = \mod_quiz\quiz_settings::create((int) $quiz->id);
            $quizsettings->get_grade_calculator()->recompute_quiz_sumgrades();
        }

        return [
            'cmid'         => (int) $cmid,
            'quiz_id'      => (int) $quiz->id,
            'created'      => true,
            'question_ids' => $createdqids,
            'view_url'     => (new \moodle_url('/mod/quiz/view.php', ['id' => $cmid]))->out(false),
            'eval_meta'    => $evalmeta,
        ];
    }

    /**
     * Insert into course_modules + course_sections.sequence so the quiz shows
     * up under the requested section. Mirrors the relevant slice of
     * `add_moduleinfo` without invoking its form pipeline.
     */
    private static function add_to_course(stdClass $course, int $sectionnum, int $moduleid, int $instanceid, string $name): int {
        global $DB, $CFG;
        require_once($CFG->dirroot . '/course/lib.php');

        // Make sure the section exists.
        $section = $DB->get_record('course_sections', ['course' => $course->id, 'section' => $sectionnum]);
        if (!$section) {
            course_create_sections_if_missing($course, [$sectionnum]);
            $section = $DB->get_record('course_sections', ['course' => $course->id, 'section' => $sectionnum], '*', MUST_EXIST);
        }

        $cm = new stdClass();
        $cm->course             = $course->id;
        $cm->module             = $moduleid;
        $cm->instance           = $instanceid;
        $cm->section            = $section->id;
        $cm->idnumber           = '';
        $cm->added              = time();
        $cm->visible            = 1;
        $cm->visibleold         = 1;
        $cm->visibleoncoursepage = 1;
        $cm->groupmode          = 0;
        $cm->groupingid         = 0;
        $cm->completion         = 0;
        $cm->completiongradeitemnumber = null;
        $cm->completionview     = 0;
        $cm->completionexpected = 0;
        $cm->showdescription    = 0;
        $cm->availability       = null;
        $cm->deletioninprogress = 0;
        $cm->lang               = '';
        $cmid = add_course_module($cm);

        // Append cmid to the section's sequence list.
        course_add_cm_to_section($course, $cmid, $sectionnum);

        \context_module::instance($cmid);
        rebuild_course_cache($course->id, true);

        return (int) $cmid;
    }

    private static function find_quiz_cm_in_section(int $courseid, int $sectionnum, string $name): ?stdClass {
        global $DB;
        $module = $DB->get_record('modules', ['name' => 'quiz'], '*', MUST_EXIST);
        $sql = "SELECT cm.*
                  FROM {course_modules} cm
                  JOIN {quiz} q ON q.id = cm.instance
                  JOIN {course_sections} s ON s.id = cm.section
                 WHERE cm.course = :courseid
                   AND cm.module = :modid
                   AND s.section = :sectionnum
                   AND q.name = :name";
        $r = $DB->get_record_sql($sql, [
            'courseid'   => $courseid,
            'modid'      => $module->id,
            'sectionnum' => $sectionnum,
            'name'       => $name,
        ]);
        return $r ?: null;
    }

    private static function list_quiz_question_ids(int $quizid): array {
        global $DB, $CFG;
        // Moodle 4+ stores quiz questions via quiz_slots → question_references → question_versions → questions.
        $sql = "SELECT q.id
                  FROM {quiz_slots} qs
                  JOIN {question_references} qr
                       ON qr.itemid = qs.id
                      AND qr.component = 'mod_quiz'
                      AND qr.questionarea = 'slot'
                  JOIN {question_bank_entries} qbe ON qbe.id = qr.questionbankentryid
                  JOIN {question_versions} qv ON qv.questionbankentryid = qbe.id
                  JOIN {question} q ON q.id = qv.questionid
                 WHERE qs.quizid = :quizid
                 ORDER BY qs.slot ASC";
        $rows = $DB->get_records_sql($sql, ['quizid' => $quizid]);
        return array_values(array_map(fn($r) => (int) $r->id, $rows));
    }

    private static function collect_eval_meta(array $questionids): array {
        global $DB;
        if (empty($questionids)) {
            return [];
        }
        list($insql, $inparams) = $DB->get_in_or_equal($questionids, SQL_PARAMS_NAMED);
        $rows = $DB->get_records_select('question', "id $insql", $inparams, '', 'id, generalfeedback');
        $out = [];
        foreach ($rows as $row) {
            $meta = json_decode($row->generalfeedback ?: '{}', true);
            $out[] = [
                'question_id'     => (int) $row->id,
                'must_cite'       => (string) ($meta['must_cite'] ?? ''),
                'expected_topics' => array_values((array) ($meta['expected_topics'] ?? [])),
            ];
        }
        return $out;
    }

    private static function ensure_course_question_category(stdClass $course, \context_course $coursectx): int {
        global $DB;
        $existing = $DB->get_record('question_categories', [
            'contextid' => $coursectx->id,
            'idnumber'  => 'neuro_synth',
        ]);
        if ($existing) {
            return (int) $existing->id;
        }
        $cat = new stdClass();
        $cat->name        = 'Neuro synthetic';
        $cat->info        = 'Auto-created by local_neurollm. Do not delete while synthetic quizzes exist.';
        $cat->infoformat  = FORMAT_HTML;
        $cat->contextid   = $coursectx->id;
        $cat->parent      = self::resolve_parent_category($coursectx);
        $cat->sortorder   = 999;
        $cat->idnumber    = 'neuro_synth';
        $cat->stamp       = make_unique_id_code();
        $cat->id = $DB->insert_record('question_categories', $cat);
        return (int) $cat->id;
    }

    private static function resolve_parent_category(\context_course $coursectx): int {
        global $DB;
        // Default question category at course context (created with the course).
        $top = $DB->get_record('question_categories', [
            'contextid' => $coursectx->id,
            'parent'    => 0,
        ], 'id', IGNORE_MULTIPLE);
        return $top ? (int) $top->id : 0;
    }

    private static function create_multichoice_question(
        int $categoryid,
        stdClass $course,
        int $idx,
        string $stem,
        array $options,
        int $correctindex,
        string $mustcite,
        array $expectedtopics
    ): int {
        global $DB, $USER;

        if (count($options) < 2) {
            throw new \invalid_parameter_exception('Each question needs at least 2 options.');
        }
        if ($correctindex < 0 || $correctindex >= count($options)) {
            $correctindex = 0;
        }

        // Encode synthetic ground truth in `generalfeedback` so the eval route
        // can read it back via WS without needing a sidecar table.
        $meta = json_encode([
            'must_cite'       => $mustcite,
            'expected_topics' => array_values($expectedtopics),
            'source'          => 'neuro_synth',
        ]);

        $form = new stdClass();
        $form->category               = $categoryid;
        $form->name                   = self::truncate_name($stem, 60) . ' #' . ($idx + 1);
        $form->questiontext           = ['text' => $stem, 'format' => FORMAT_HTML, 'itemid' => 0];
        $form->generalfeedback        = ['text' => $meta, 'format' => FORMAT_HTML, 'itemid' => 0];
        $form->defaultmark            = 1.0;
        $form->penalty                = 0.3333333;
        $form->qtype                  = 'multichoice';
        $form->status                 = 'ready';
        $form->idnumber               = '';

        $form->single                 = 1;
        $form->shuffleanswers         = 1;
        $form->answernumbering        = 'abc';
        $form->correctfeedback        = ['text' => 'Correct.', 'format' => FORMAT_HTML, 'itemid' => 0];
        $form->partiallycorrectfeedback = ['text' => 'Partially correct.', 'format' => FORMAT_HTML, 'itemid' => 0];
        $form->incorrectfeedback      = ['text' => 'Review the cited section.', 'format' => FORMAT_HTML, 'itemid' => 0];
        $form->shownumcorrect         = 1;
        $form->showstandardinstruction = 0;

        $form->answer   = [];
        $form->fraction = [];
        $form->feedback = [];
        foreach ($options as $i => $opt) {
            $form->answer[$i]   = ['text' => (string) $opt, 'format' => FORMAT_HTML, 'itemid' => 0];
            $form->fraction[$i] = ($i === $correctindex) ? '1.0' : '0.0';
            $form->feedback[$i] = ['text' => '', 'format' => FORMAT_HTML, 'itemid' => 0];
        }

        $oldquestion = new stdClass();
        $oldquestion->category = $categoryid;
        $oldquestion->qtype    = 'multichoice';
        $oldquestion->createdby = $USER->id;

        $qtype = \question_bank::get_qtype('multichoice');
        $newquestion = $qtype->save_question($oldquestion, $form);
        return (int) $newquestion->id;
    }

    private static function truncate_name(string $stem, int $max): string {
        $clean = trim(strip_tags($stem));
        if (strlen($clean) <= $max) {
            return $clean;
        }
        return substr($clean, 0, $max - 1) . '…';
    }
}
