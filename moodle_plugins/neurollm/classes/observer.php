<?php
// Event observer that POSTs a JSON webhook to the neuro-moodle-llm FastAPI
// service when a learner submits a quiz attempt. The API correlates the
// attempt with synthetic ground truth and runs the agentic-feedback eval.
//
// Uses curl (Moodle ships its own curl wrapper) with a short timeout so a
// dead API doesn't slow Moodle down. Failures are logged via debugging().

namespace local_neurollm;

defined('MOODLE_INTERNAL') || die();

class observer {

    /**
     * @param \mod_quiz\event\attempt_submitted $event
     */
    public static function quiz_attempt_submitted(\mod_quiz\event\attempt_submitted $event): void {
        $apibase = trim((string) get_config('local_neurollm', 'api_base_url'));
        if ($apibase === '') {
            return; // Plugin not configured; silently drop.
        }
        // The browser-facing setting (e.g. http://localhost:8888) doesn't work
        // from inside the moodle container; allow an override via setting
        // `webhook_base_url` (e.g. http://neuro-moodle-llm:8888).
        $webhookbase = trim((string) get_config('local_neurollm', 'webhook_base_url')) ?: $apibase;

        $secret = trim((string) get_config('local_neurollm', 'event_secret'));

        $payload = [
            'eventname'   => $event->eventname,
            'courseid'    => (int) $event->courseid,
            'objecttable' => 'quiz_attempts',
            'objectid'    => (int) $event->objectid,
            'contextid'   => (int) $event->contextid,
            'userid'      => (int) $event->userid,
            'timecreated' => (int) $event->timecreated,
            'secret'      => $secret,
        ];

        $url = rtrim($webhookbase, '/') . '/v1/events/moodle';

        $curl = new \curl();
        $curl->setHeader(['Content-Type: application/json']);
        $curl->setopt(['CURLOPT_TIMEOUT' => 4, 'CURLOPT_CONNECTTIMEOUT' => 2]);
        $resp = $curl->post($url, json_encode($payload));
        $info = $curl->get_info();
        $http = (int) ($info['http_code'] ?? 0);
        if ($http < 200 || $http >= 300) {
            debugging('local_neurollm: webhook ' . $url . ' returned HTTP ' . $http . ': ' . substr((string) $resp, 0, 300), DEBUG_DEVELOPER);
        }
    }
}
