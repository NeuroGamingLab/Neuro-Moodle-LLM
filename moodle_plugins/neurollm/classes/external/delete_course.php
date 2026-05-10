<?php
// Delete a synthetic course.
//
// Hard guard: only courses whose `idnumber` starts with `synth-` (i.e. were
// created by `create_course`) can be deleted via this WS. This prevents
// accidental deletion of real Moodle content from the integration.

namespace local_neurollm\external;

defined('MOODLE_INTERNAL') || die();

global $CFG;
require_once($CFG->dirroot . '/course/lib.php');

use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_single_structure;
use core_external\external_value;

class delete_course extends external_api {

    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'course_id' => new external_value(PARAM_INT, 'Course id to delete'),
        ]);
    }

    public static function execute_returns(): external_single_structure {
        return new external_single_structure([
            'deleted'   => new external_value(PARAM_BOOL, 'True if the course was deleted'),
            'course_id' => new external_value(PARAM_INT, 'Course id'),
            'shortname' => new external_value(PARAM_TEXT, 'Course shortname (pre-deletion)'),
        ]);
    }

    public static function execute(int $courseid): array {
        global $DB;
        $params = self::validate_parameters(self::execute_parameters(), ['course_id' => $courseid]);

        $course = get_course($params['course_id']);
        $context = \context_course::instance($course->id);
        self::validate_context($context);
        require_capability('moodle/course:delete', $context);

        if (strpos((string) $course->idnumber, 'synth-') !== 0) {
            throw new \moodle_exception('invalidcourse', 'error', '',
                'Refusing to delete: course idnumber does not start with "synth-".');
        }

        delete_course($course, false);

        return [
            'deleted'   => true,
            'course_id' => (int) $course->id,
            'shortname' => (string) $course->shortname,
        ];
    }
}
