<?php
// This file is part of Moodle - http://moodle.org/
//
// Thin local plugin: zero ML logic. Links instructors to the external
// Neuro-Moodle-LLM API (FastAPI) running outside Moodle.

defined('MOODLE_INTERNAL') || die();

$plugin->version   = 2026051001;
$plugin->requires  = 2024042200;
$plugin->component = 'local_neurollm';
$plugin->maturity  = MATURITY_ALPHA;
$plugin->release    = '0.2.0';
