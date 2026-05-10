<?php
// One-shot bootstrap: enable Moodle web services + REST, create the
// `neurollm` external service, the `ws_neurollm` service user, and mint a
// permanent token. Idempotent: safe to re-run.
//
//   docker compose exec moodle php /tmp/bootstrap-webservice.php
//
// Prints a single line on success:
//   MOODLE_TOKEN=<token>

define('CLI_SCRIPT', true);
$configfile = getenv('MOODLE_CONFIG') ?: '/var/www/html/config.php';
require($configfile);
require_once($CFG->libdir . '/clilib.php');
require_once($CFG->libdir . '/accesslib.php');
require_once($CFG->dirroot . '/user/lib.php');
require_once($CFG->dirroot . '/webservice/lib.php');

global $DB, $USER;

// Run as the site admin so generate_token() has a valid creator.
\core\session\manager::set_user(get_admin());

// 1. Enable web services + the REST protocol.
set_config('enablewebservices', 1);
$protocols = array_filter(array_map('trim', explode(',', (string)($CFG->webserviceprotocols ?? ''))));
if (!in_array('rest', $protocols, true)) {
    $protocols[] = 'rest';
    set_config('webserviceprotocols', implode(',', array_unique($protocols)));
}

// 2. Create (or fetch) the dedicated external service.
$shortname = 'neurollm';
$service = $DB->get_record('external_services', ['shortname' => $shortname]);
if (!$service) {
    $service = (object) [
        'name' => 'Neuro Moodle LLM',
        'shortname' => $shortname,
        'enabled' => 1,
        'restrictedusers' => 1,
        'downloadfiles' => 1,
        'uploadfiles' => 0,
        'timecreated' => time(),
        'timemodified' => time(),
    ];
    $service->id = $DB->insert_record('external_services', $service);
    $service = $DB->get_record('external_services', ['id' => $service->id], '*', MUST_EXIST);
}

// 3. Authorise the WS functions the integration needs.
//
// We list local_neurollm_* functions explicitly because Moodle's plugin
// upgrade only auto-attaches a function to services declared by the SAME
// plugin's db/services.php, and the `neurollm` service is owned by this
// bootstrap (not the plugin) so we wire them up directly.
$wsfunctions = [
    'core_webservice_get_site_info',
    'core_course_get_courses',
    'core_course_get_courses_by_field',
    'core_course_get_contents',
    'core_course_get_categories',
    'core_course_create_categories',
    'core_course_create_courses',
    'core_course_delete_courses',
    'core_course_edit_section',
    'core_enrol_get_users_courses',
    'mod_assign_get_assignments',
    'mod_assign_get_submissions',
    'mod_assign_save_grade',
    'mod_page_get_pages_by_courses',
    'mod_resource_get_resources_by_courses',
    'mod_url_get_urls_by_courses',
    'mod_book_get_books_by_courses',
    'mod_forum_get_forums_by_courses',
    'mod_quiz_get_user_attempts',
    'mod_quiz_get_attempt_review',
    'core_files_get_files',
    // Custom write/read functions provided by the local_neurollm plugin
    // (declared in moodle_plugins/neurollm/db/services.php). Attached here
    // so they ride the same `neurollm` service + token as the core calls.
    'local_neurollm_create_course',
    'local_neurollm_create_page',
    'local_neurollm_create_quiz_with_questions',
    'local_neurollm_delete_course',
    'local_neurollm_get_quiz_attempt',
];
foreach ($wsfunctions as $fname) {
    $exists = $DB->record_exists('external_services_functions', [
        'externalserviceid' => $service->id,
        'functionname' => $fname,
    ]);
    if (!$exists) {
        $DB->insert_record('external_services_functions', (object) [
            'externalserviceid' => $service->id,
            'functionname' => $fname,
        ]);
    }
}

// 4. Create (or fetch) the dedicated service user.
$username = 'ws_neurollm';
$user = $DB->get_record('user', [
    'username' => $username,
    'mnethostid' => $CFG->mnet_localhost_id,
]);
if (!$user) {
    $newuser = new stdClass();
    $newuser->username = $username;
    $newuser->firstname = 'Neuro';
    $newuser->lastname = 'WebService';
    $newuser->email = 'ws_neurollm@example.local';
    $newuser->auth = 'manual';
    $newuser->confirmed = 1;
    $newuser->mnethostid = $CFG->mnet_localhost_id;
    $newuser->lang = 'en';
    $newuser->timezone = 'UTC';
    $newuser->password = bin2hex(random_bytes(16)) . 'A1!a'; // rotated on next step
    $newuser->id = user_create_user($newuser, true, false);
    $user = $DB->get_record('user', ['id' => $newuser->id], '*', MUST_EXIST);
}

// 5. Assign Manager at system context (broad; tighten later via custom role).
$context = context_system::instance();
$managerrole = $DB->get_record('role', ['shortname' => 'manager'], '*', MUST_EXIST);
if (!user_has_role_assignment($user->id, $managerrole->id, $context->id)) {
    role_assign($managerrole->id, $user->id, $context->id);
}

// 5b. webservice/rest:use has no default archetype in Moodle 5 — grant it
// explicitly so the manager role can authenticate via the REST protocol.
assign_capability('webservice/rest:use', CAP_ALLOW, $managerrole->id, $context->id, true);
$context->mark_dirty();

// 6. Authorise this user on the service (restrictedusers = 1 requires this row).
$existing = $DB->get_record('external_services_users', [
    'externalserviceid' => $service->id,
    'userid' => $user->id,
]);
if (!$existing) {
    $DB->insert_record('external_services_users', (object) [
        'externalserviceid' => $service->id,
        'userid' => $user->id,
        'iprestriction' => '',
        'validuntil' => 0,
        'timecreated' => time(),
    ]);
}

// 7. Re-use an existing permanent token if present, otherwise mint one.
$token = $DB->get_field_sql(
    "SELECT token FROM {external_tokens}
      WHERE userid = :uid AND externalserviceid = :sid AND tokentype = :type
      ORDER BY id DESC",
    ['uid' => $user->id, 'sid' => $service->id, 'type' => EXTERNAL_TOKEN_PERMANENT]
);
if (!$token) {
    $token = \core_external\util::generate_token(
        EXTERNAL_TOKEN_PERMANENT,
        $service,
        $user->id,
        $context,
        0,
        '',
        'neurollm-bootstrap'
    );
}

echo "MOODLE_TOKEN=" . $token . PHP_EOL;
