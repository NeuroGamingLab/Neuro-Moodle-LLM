<?php
// Event observers — Moodle calls our class on each named event.
//
// Currently we only listen for quiz_attempt_submitted so the agentic
// feedback citation eval can run automatically against synthetic quizzes.
// Re-uses the same shared-secret-protected webhook the events.py poller hits.

defined('MOODLE_INTERNAL') || die();

$observers = [
    [
        'eventname' => '\mod_quiz\event\attempt_submitted',
        'callback'  => '\local_neurollm\observer::quiz_attempt_submitted',
        'priority'  => 10,
        'internal'  => false, // run after the transaction so the attempt row exists
    ],
];
