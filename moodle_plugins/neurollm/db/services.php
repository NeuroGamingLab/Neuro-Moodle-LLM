<?php
// External function declarations.
//
// All functions are auto-attached to the `neurollm` external service so the
// existing `ws_neurollm` token gains access on plugin upgrade — no need to
// re-run the bootstrap WS bootstrapper.

defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_neurollm_create_course' => [
        'classname'   => 'local_neurollm\external\create_course',
        'methodname'  => 'execute',
        'description' => 'Create (or fetch existing) a synthetic course shell. Idempotent by shortname.',
        'type'        => 'write',
        'capabilities' => 'moodle/course:create',
        'ajax'        => false,
        'services'    => ['neurollm'],
    ],
    'local_neurollm_create_page' => [
        'classname'   => 'local_neurollm\external\create_page',
        'methodname'  => 'execute',
        'description' => 'Add a Page resource to a course section. Idempotent by section + name.',
        'type'        => 'write',
        'capabilities' => 'moodle/course:manageactivities',
        'ajax'        => false,
        'services'    => ['neurollm'],
    ],
    'local_neurollm_create_quiz_with_questions' => [
        'classname'   => 'local_neurollm\external\create_quiz_with_questions',
        'methodname'  => 'execute',
        'description' => 'Create a Quiz activity in a course section, populated with multichoice questions.',
        'type'        => 'write',
        'capabilities' => 'moodle/course:manageactivities,moodle/question:add',
        'ajax'        => false,
        'services'    => ['neurollm'],
    ],
    'local_neurollm_delete_course' => [
        'classname'   => 'local_neurollm\external\delete_course',
        'methodname'  => 'execute',
        'description' => 'Delete a synthetic course (must have idnumber starting with synth-).',
        'type'        => 'write',
        'capabilities' => 'moodle/course:delete',
        'ajax'        => false,
        'services'    => ['neurollm'],
    ],
    'local_neurollm_get_quiz_attempt' => [
        'classname'   => 'local_neurollm\external\get_quiz_attempt',
        'methodname'  => 'execute',
        'description' => 'Return submitted-attempt detail (questions + learner answers + correctness).',
        'type'        => 'read',
        'capabilities' => 'mod/quiz:viewreports',
        'ajax'        => false,
        'services'    => ['neurollm'],
    ],
];

// NB: the `neurollm` external service itself is created/owned by
// `docker/bootstrap-webservice.php` (so it exists before the plugin is
// installed). We deliberately do NOT redeclare it here — each function above
// auto-attaches via its `services => ['neurollm']` entry, which Moodle's
// `external_update_descriptions()` honours by inserting the missing rows
// into `external_services_functions`.
