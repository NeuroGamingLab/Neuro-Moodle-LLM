<?php
// CLI helper: submit a quiz attempt as the admin user, picking the correct
// option (index 0) for every multichoice slot. Used for end-to-end smoke
// tests of the Phase C closed-loop event eval.
//
// Args: --quiz=<quizid>

define('CLI_SCRIPT', true);
require('/var/www/html/config.php');
require_once($CFG->libdir . '/clilib.php');
require_once($CFG->dirroot . '/mod/quiz/locallib.php');

list($options, $unrecognised) = cli_get_params(['quiz' => null], ['q' => 'quiz']);
$quizid = (int) $options['quiz'];
if (!$quizid) {
    cli_error('--quiz=<id> required');
}

\core\session\manager::set_user(get_admin());

global $DB, $USER;
$quiz = $DB->get_record('quiz', ['id' => $quizid], '*', MUST_EXIST);
$quizobj = \mod_quiz\quiz_settings::create($quiz->id, $USER->id);
$quizobj->preload_questions();
$quizobj->load_questions();
$timenow = time();

// Pick the next attempt number for this user/quiz so we don't collide with
// any attempts left over from earlier dev runs.
$existing = (int) $DB->get_field_sql(
    'SELECT MAX(attempt) FROM {quiz_attempts} WHERE quiz = :quiz AND userid = :userid',
    ['quiz' => $quizid, 'userid' => $USER->id]
);
$nextno = max(1, $existing + 1);

try {
    $attempt = quiz_create_attempt($quizobj, $nextno, false, $timenow, false, $USER->id);
    $quba = \question_engine::make_questions_usage_by_activity('mod_quiz', $quizobj->get_context());
    $quba->set_preferred_behaviour($quiz->preferredbehaviour);
    $attempt = quiz_start_new_attempt($quizobj, $quba, $attempt, 1, $timenow);
    $attempt = quiz_attempt_save_started($quizobj, $quba, $attempt);
} catch (\Throwable $e) {
    cli_writeln('FATAL: ' . get_class($e) . ' :: ' . $e->getMessage());
    if (property_exists($e, 'debuginfo') && $e->debuginfo) {
        cli_writeln('debuginfo: ' . $e->debuginfo);
    }
    cli_writeln($e->getTraceAsString());
    exit(2);
}

$attemptobj = \mod_quiz\quiz_attempt::create($attempt->id);
$payload = [];
foreach ($quba->get_slots() as $slot) {
    $payload[$slot] = ['answer' => 0]; // index 0 = the correct option (we always seed it that way)
}
$attemptobj->process_submitted_actions(time(), false, $payload);

// Submit + finish the attempt (depends on Moodle version's API).
if (method_exists($attemptobj, 'process_submit')) {
    $attemptobj->process_submit(time(), false, true);
} else {
    $attemptobj->process_finish(time(), false);
}
if (method_exists($attemptobj, 'process_grade_submission')) {
    $attemptobj->process_grade_submission(time());
}

cli_writeln('attempt_id=' . $attempt->id);
