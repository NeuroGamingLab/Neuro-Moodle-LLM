<?php
// Navigation hook — adds a course navigation entry pointing at the local page.

defined('MOODLE_INTERNAL') || die();

/**
 * Extend course navigation with a link to the thin wrapper page.
 *
 * @param navigation_node $coursenode
 * @param stdClass $course
 * @param context_course $context
 */
function local_neurollm_extend_navigation_course($coursenode, $course, $context) {
    if ($context->contextlevel != CONTEXT_COURSE) {
        return;
    }
    $url = new moodle_url('/local/neurollm/index.php', ['id' => $course->id]);
    $coursenode->add(
        get_string('navlabel', 'local_neurollm'),
        $url,
        navigation_node::TYPE_CUSTOM,
        null,
        'local_neurollm',
        new pix_icon('i/settings', '')
    );
}
