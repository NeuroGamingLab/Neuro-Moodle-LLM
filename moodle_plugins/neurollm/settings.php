<?php
// Admin settings: API base URL (browser-reachable).
// Moodle invokes this file with $ADMIN in scope; standard pattern below.

defined('MOODLE_INTERNAL') || die();

if ($hassiteconfig) {
    $settings = new admin_settingpage(
        'local_neurollm',
        get_string('pluginname', 'local_neurollm')
    );

    $settings->add(new admin_setting_configtext(
        'local_neurollm/api_base_url',
        get_string('api_base_url', 'local_neurollm'),
        get_string('api_base_url_desc', 'local_neurollm'),
        'http://localhost:8888',
        PARAM_URL
    ));

    $settings->add(new admin_setting_configtext(
        'local_neurollm/webhook_base_url',
        get_string('webhook_base_url', 'local_neurollm'),
        get_string('webhook_base_url_desc', 'local_neurollm'),
        'http://neuro-moodle-llm:8888',
        PARAM_URL
    ));

    $settings->add(new admin_setting_configpasswordunmask(
        'local_neurollm/event_secret',
        get_string('event_secret', 'local_neurollm'),
        get_string('event_secret_desc', 'local_neurollm'),
        ''
    ));

    $ADMIN->add('localplugins', $settings);
}
