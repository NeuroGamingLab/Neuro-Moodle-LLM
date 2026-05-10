<?php
// Return all interesting bits of a submitted quiz attempt in one round-trip:
// stem, options, learner answer, correctness flag, and the synthetic ground
// truth we encoded in `generalfeedback` at create time (must_cite +
// expected_topics). Used by the agentic-feedback citation eval loop.

namespace local_neurollm\external;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->dirroot . '/mod/quiz/locallib.php');
require_once($CFG->dirroot . '/question/engine/lib.php');

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;

class get_quiz_attempt extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'attempt_id' => new external_value(PARAM_INT, 'mdl_quiz_attempts.id'),
        ]);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'attempt_id' => new external_value(PARAM_INT),
            'quiz_id'    => new external_value(PARAM_INT),
            'course_id'  => new external_value(PARAM_INT),
            'user_id'    => new external_value(PARAM_INT),
            'state'      => new external_value(PARAM_TEXT),
            'sumgrades'  => new external_value(PARAM_FLOAT),
            'questions'  => new external_multiple_structure(
                new external_single_structure([
                    'question_id'     => new external_value(PARAM_INT),
                    'slot'            => new external_value(PARAM_INT),
                    'stem_html'       => new external_value(PARAM_RAW),
                    'response_summary' => new external_value(PARAM_TEXT, 'Learner response as text', VALUE_DEFAULT, ''),
                    'right_answer'    => new external_value(PARAM_TEXT, 'Correct answer summary', VALUE_DEFAULT, ''),
                    'mark'            => new external_value(PARAM_FLOAT),
                    'max_mark'        => new external_value(PARAM_FLOAT),
                    'correct'         => new external_value(PARAM_BOOL),
                    'must_cite'       => new external_value(PARAM_TEXT),
                    'expected_topics' => new external_multiple_structure(new external_value(PARAM_TEXT)),
                ])
            ),
        ]);
    }

    public static function execute(int $attemptid): array {
        global $DB;
        $params = self::validate_parameters(self::execute_parameters(), ['attempt_id' => $attemptid]);

        $attempt = $DB->get_record('quiz_attempts', ['id' => $params['attempt_id']], '*', MUST_EXIST);
        $quiz = $DB->get_record('quiz', ['id' => $attempt->quiz], '*', MUST_EXIST);
        $course = get_course($quiz->course);
        $cm = get_coursemodule_from_instance('quiz', $quiz->id, $course->id, false, MUST_EXIST);
        $context = \context_module::instance($cm->id);
        self::validate_context($context);
        require_capability('mod/quiz:viewreports', $context);

        $quba = \question_engine::load_questions_usage_by_activity($attempt->uniqueid);

        $rows = [];
        foreach ($quba->get_slots() as $slot) {
            $qa = $quba->get_question_attempt($slot);
            $question = $qa->get_question();
            $maxmark = (float) $qa->get_max_mark();
            $mark = (float) ($qa->get_mark() ?? 0);
            $correct = $maxmark > 0 ? abs($mark - $maxmark) < 1e-6 : false;

            // Pull our encoded synthetic ground truth out of generalfeedback.
            $must = '';
            $topics = [];
            $row = $DB->get_record('question', ['id' => $question->id], 'id, generalfeedback');
            if ($row && $row->generalfeedback) {
                $meta = json_decode($row->generalfeedback, true);
                if (is_array($meta)) {
                    $must = (string) ($meta['must_cite'] ?? '');
                    $topics = array_values((array) ($meta['expected_topics'] ?? []));
                }
            }

            $rows[] = [
                'question_id'      => (int) $question->id,
                'slot'             => (int) $slot,
                'stem_html'        => (string) $question->questiontext,
                'response_summary' => (string) ($qa->get_response_summary() ?? ''),
                'right_answer'     => (string) ($qa->get_right_answer_summary() ?? ''),
                'mark'             => $mark,
                'max_mark'         => $maxmark,
                'correct'          => $correct,
                'must_cite'        => $must,
                'expected_topics'  => $topics,
            ];
        }

        return [
            'attempt_id' => (int) $attempt->id,
            'quiz_id'    => (int) $quiz->id,
            'course_id'  => (int) $course->id,
            'user_id'    => (int) $attempt->userid,
            'state'      => (string) $attempt->state,
            'sumgrades'  => (float) ($attempt->sumgrades ?? 0),
            'questions'  => $rows,
        ];
    }
}
