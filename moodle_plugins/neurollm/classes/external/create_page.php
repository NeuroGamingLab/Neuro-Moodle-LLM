<?php
// Add a Page resource (mod_page) to a course section.
//
// Idempotent: if a page with the same name already exists in the same
// section it's updated in place. The section's name and summary can also
// be updated as part of the same call (handy for "Week N: Title" headers).

namespace local_neurollm\external;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/course/modlib.php');

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;
use stdClass;

class create_page extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'course_id'      => new external_value(PARAM_INT, 'Target Moodle course id'),
            'section_num'    => new external_value(PARAM_INT, 'Section number (0=topic 0; 1=Week 1; etc.)'),
            'section_name'   => new external_value(PARAM_TEXT, 'Optional new name for the section', VALUE_DEFAULT, ''),
            'section_summary' => new external_value(PARAM_RAW, 'Optional new HTML summary for the section', VALUE_DEFAULT, ''),
            'name'           => new external_value(PARAM_TEXT, 'Page name shown in the section'),
            'content_html'   => new external_value(PARAM_RAW, 'Page body as HTML'),
            'visible'        => new external_value(PARAM_BOOL, 'Visible to learners?', VALUE_DEFAULT, true),
        ]);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'cmid'      => new external_value(PARAM_INT, 'Course module id'),
            'instance'  => new external_value(PARAM_INT, 'Page instance id'),
            'section_id' => new external_value(PARAM_INT, 'course_sections.id'),
            'created'   => new external_value(PARAM_BOOL, 'True if newly created'),
            'view_url'  => new external_value(PARAM_URL, 'Browser URL to view the page'),
        ]);
    }

    public static function execute(
        int $courseid,
        int $sectionnum,
        string $sectionname = '',
        string $sectionsummary = '',
        string $name = '',
        string $contenthtml = '',
        bool $visible = true
    ): array {
        global $DB, $CFG;

        $params = self::validate_parameters(self::execute_parameters(), [
            'course_id'       => $courseid,
            'section_num'     => $sectionnum,
            'section_name'    => $sectionname,
            'section_summary' => $sectionsummary,
            'name'            => $name,
            'content_html'    => $contenthtml,
            'visible'         => $visible,
        ]);

        $course = get_course($params['course_id']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        require_capability('moodle/course:manageactivities', $context);

        // Ensure the requested section exists; create it if needed.
        $section = $DB->get_record('course_sections', [
            'course' => $course->id,
            'section' => $params['section_num'],
        ]);
        if (!$section) {
            course_create_sections_if_missing($course, [(int) $params['section_num']]);
            $section = $DB->get_record('course_sections', [
                'course' => $course->id,
                'section' => $params['section_num'],
            ], '*', MUST_EXIST);
        }

        // Optionally update section name + summary.
        $updates = [];
        if ($params['section_name'] !== '') {
            $updates['name'] = $params['section_name'];
        }
        if ($params['section_summary'] !== '') {
            $updates['summary'] = $params['section_summary'];
            $updates['summaryformat'] = FORMAT_HTML;
        }
        if (!empty($updates)) {
            course_update_section($course, $section, $updates);
            $section = $DB->get_record('course_sections', ['id' => $section->id], '*', MUST_EXIST);
        }

        // Look for an existing Page in this section with the same name.
        $existingcm = self::find_page_cm_in_section($course->id, $section->id, $params['name']);

        if ($existingcm) {
            $page = $DB->get_record('page', ['id' => $existingcm->instance], '*', MUST_EXIST);
            $page->content        = $params['content_html'];
            $page->contentformat  = FORMAT_HTML;
            $page->revision       = ((int) $page->revision) + 1;
            $page->timemodified   = time();
            $DB->update_record('page', $page);
            // Update visibility.
            set_coursemodule_visible($existingcm->id, $params['visible'] ? 1 : 0);

            return [
                'cmid'       => (int) $existingcm->id,
                'instance'   => (int) $page->id,
                'section_id' => (int) $section->id,
                'created'    => false,
                'view_url'   => (new \moodle_url('/mod/page/view.php', ['id' => $existingcm->id]))->out(false),
            ];
        }

        // Insert directly into mdl_page + wire up the course_module + section.
        // We bypass add_moduleinfo() because Moodle's mod_form pipeline strips
        // editor-style fields (`$mod->page['text']`) when called outside a real
        // form submission, leaving Page resources with empty content.
        $module = $DB->get_record('modules', ['name' => 'page'], '*', MUST_EXIST);

        $page = new stdClass();
        $page->course             = $course->id;
        $page->name               = $params['name'];
        $page->intro              = '';
        $page->introformat        = FORMAT_HTML;
        $page->content            = $params['content_html'];
        $page->contentformat      = FORMAT_HTML;
        $page->legacyfiles        = 0;
        $page->legacyfileslast    = null;
        $page->display            = 5; // RESOURCELIB_DISPLAY_OPEN
        $page->displayoptions     = serialize(['printintro' => 0, 'printlastmodified' => 1]);
        $page->revision           = 1;
        $page->timemodified       = time();
        $page->id = $DB->insert_record('page', $page);

        $cmid = self::add_to_course($course, (int) $params['section_num'], (int) $module->id, (int) $page->id, $params['name'], (bool) $params['visible']);

        return [
            'cmid'       => (int) $cmid,
            'instance'   => (int) $page->id,
            'section_id' => (int) $section->id,
            'created'    => true,
            'view_url'   => (new \moodle_url('/mod/page/view.php', ['id' => $cmid]))->out(false),
        ];
    }

    /**
     * Insert a course_modules row + wire it into the section's sequence.
     * Returns the new cmid.
     */
    private static function add_to_course(stdClass $course, int $sectionnum, int $moduleid, int $instanceid, string $name, bool $visible): int {
        global $DB, $CFG;
        require_once($CFG->dirroot . '/course/lib.php');

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
        $cm->visible            = $visible ? 1 : 0;
        $cm->visibleold         = $visible ? 1 : 0;
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

        course_add_cm_to_section($course, $cmid, $sectionnum);
        \context_module::instance($cmid);
        rebuild_course_cache($course->id, true);
        return (int) $cmid;
    }

    private static function find_page_cm_in_section(int $courseid, int $sectionid, string $name): ?stdClass {
        global $DB;
        $module = $DB->get_record('modules', ['name' => 'page'], '*', MUST_EXIST);
        $sql = "SELECT cm.*
                  FROM {course_modules} cm
                  JOIN {page} p ON p.id = cm.instance
                 WHERE cm.course = :courseid
                   AND cm.module = :modid
                   AND cm.section = :sectionid
                   AND p.name = :name";
        $r = $DB->get_record_sql($sql, [
            'courseid'  => $courseid,
            'modid'     => $module->id,
            'sectionid' => $sectionid,
            'name'      => $name,
        ]);
        return $r ?: null;
    }
}
