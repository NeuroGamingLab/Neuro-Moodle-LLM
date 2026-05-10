<?php
// English language pack for local_neurollm.

defined('MOODLE_INTERNAL') || die();

$string['pluginname'] = 'Neuro Moodle LLM';
$string['navlabel'] = 'Neuro ML assistant';
$string['pagetitle'] = 'Neuro Moodle LLM';
$string['chat_intro'] = 'Ask the course-aware assistant a question. Answers are grounded in this course\'s content (read-only); ML runs in a separate service outside Moodle.';
$string['ask_placeholder'] = 'e.g. What is week 1 about?';
$string['ask_button'] = 'Ask';
$string['docs_link_prefix'] = 'Operators:';
$string['opendocs'] = 'Local Ollama';
$string['api_base_url'] = 'Neuro API base URL';
$string['api_base_url_desc'] = 'Browser-reachable URL of the neuro-moodle-llm FastAPI service (e.g. http://localhost:8888 when published from Docker).';
$string['webhook_base_url'] = 'Neuro webhook base URL (server-side)';
$string['webhook_base_url_desc'] = 'URL the Moodle container uses to reach the API for event webhooks. Defaults to http://neuro-moodle-llm:8888 (Docker network alias).';
$string['event_secret'] = 'Webhook shared secret';
$string['event_secret_desc'] = 'Sent as the JSON `secret` field on event webhooks; must match NEURO_EVENT_SECRET on the FastAPI side.';
$string['not_configured'] = 'Neuro API base URL is not configured. Site administration → Plugins → Local plugins → Neuro Moodle LLM.';
