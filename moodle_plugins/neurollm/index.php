<?php
// In-Moodle chat block. No ML in PHP — calls the external API from the browser.

require(__DIR__ . '/../../../config.php');

$courseid = required_param('id', PARAM_INT);
$course = get_course($courseid);
require_login($course);

$context = context_course::instance($course->id);
require_capability('moodle/course:view', $context);

$PAGE->set_url(new moodle_url('/local/neurollm/index.php', ['id' => $courseid]));
$PAGE->set_context($context);
$PAGE->set_pagelayout('incourse');
$PAGE->set_title(get_string('pagetitle', 'local_neurollm'));
$PAGE->set_heading(format_string($course->fullname));

$apibase = trim((string) get_config('local_neurollm', 'api_base_url'));

echo $OUTPUT->header();

if ($apibase === '') {
    echo $OUTPUT->notification(
        get_string('not_configured', 'local_neurollm'),
        'notifyproblem'
    );
    echo $OUTPUT->footer();
    exit;
}

$apibase = rtrim($apibase, '/');
$docsurl = new moodle_url($apibase . '/docs');
$learner = $USER->id;

echo html_writer::start_div('local-neurollm-chat', [
    'data-api-base' => s($apibase),
    'data-course-id' => (int) $courseid,
    'data-learner-id' => (int) $learner,
]);
?>
<style>
  .local-neurollm-chat { max-width: 760px; margin: 1rem auto; font-family: var(--font-family-sans-serif, sans-serif); }
  .local-neurollm-chat .nllm-row { display: flex; gap: .5rem; margin-bottom: .75rem; }
  .local-neurollm-chat #nllm-input { flex: 1; padding: .55rem .75rem; border: 1px solid #c9c9c9; border-radius: 6px; font-size: 1rem; }
  .local-neurollm-chat .nllm-btn { padding: .55rem .9rem; border: none; border-radius: 6px; background: #7c3aed; color: #fff; cursor: pointer; }
  .local-neurollm-chat .nllm-btn:disabled { opacity: .5; cursor: wait; }
  .local-neurollm-chat .nllm-bubble { background: #12121a; color: #e8e6e3; padding: .9rem 1rem; border-radius: 8px; white-space: pre-wrap; }
  .local-neurollm-chat .nllm-bubble.you { background: #f1f0ee; color: #1a1a1a; }
  .local-neurollm-chat .nllm-meta { color: #777; font-size: .82rem; margin-top: .35rem; }
  .local-neurollm-chat .nllm-sources { font-size: .85rem; margin-top: .5rem; }
  .local-neurollm-chat .nllm-sources li { margin-bottom: .25rem; }
  .local-neurollm-chat .nllm-thumbs button { background: none; border: 1px solid #c9c9c9; border-radius: 6px; padding: .25rem .55rem; margin-right: .35rem; cursor: pointer; }
</style>
<p><?= s(get_string('chat_intro', 'local_neurollm')) ?></p>
<div class="nllm-row">
  <input id="nllm-input" placeholder="<?= s(get_string('ask_placeholder', 'local_neurollm')) ?>" />
  <button id="nllm-send" class="nllm-btn"><?= s(get_string('ask_button', 'local_neurollm')) ?></button>
</div>
<div id="nllm-thread"></div>
<p class="nllm-meta">
  <?= s(get_string('docs_link_prefix', 'local_neurollm')) ?>
  <a href="<?= s($docsurl) ?>" target="_blank" rel="noopener noreferrer"><?= s(get_string('opendocs', 'local_neurollm')) ?></a>
</p>
<script>
(function () {
  var root = document.querySelector('.local-neurollm-chat');
  var apiBase = root.dataset.apiBase;
  var courseId = parseInt(root.dataset.courseId, 10);
  var learnerId = String(root.dataset.learnerId);
  var input = document.getElementById('nllm-input');
  var send = document.getElementById('nllm-send');
  var thread = document.getElementById('nllm-thread');

  function bubble(cls, html) {
    var d = document.createElement('div');
    d.className = 'nllm-bubble ' + cls;
    d.innerHTML = html;
    thread.appendChild(d);
    return d;
  }
  function escape(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
      return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
    });
  }
  function feedback(qid, value) {
    fetch(apiBase + '/v1/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ qid: qid, vote: value, learner_id: learnerId, course_id: courseId })
    });
  }
  send.addEventListener('click', async function () {
    var q = input.value.trim();
    if (!q) return;
    bubble('you', '<strong>You:</strong> ' + escape(q));
    input.value = '';
    send.disabled = true;
    var slot = bubble('answer', '<em>Thinking…</em>');
    try {
      var resp = await fetch(apiBase + '/v1/rag/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, course_id: courseId, top_k: 5, learner_id: learnerId })
      });
      var data = await resp.json();
      var qid = data.qid || ('q' + Date.now());
      var srcHtml = '';
      if (Array.isArray(data.sources) && data.sources.length) {
        srcHtml = '<div class="nllm-sources"><strong>Sources:</strong><ul>'
          + data.sources.map(function (s) {
              var conf = (s.score !== undefined) ? ' (' + Number(s.score).toFixed(2) + ')' : '';
              return '<li>' + escape(s.title || '(untitled)') + conf + '</li>';
            }).join('')
          + '</ul></div>';
      }
      var meta = '<div class="nllm-meta">cache=' + escape(data.cache || 'miss')
        + ' confidence=' + (data.confidence !== undefined ? Number(data.confidence).toFixed(3) : 'n/a')
        + '</div>';
      var thumbs = '<div class="nllm-thumbs">'
        + '<button data-v="up">👍 helpful</button>'
        + '<button data-v="down">👎 unhelpful</button></div>';
      slot.innerHTML = '<strong>Assistant:</strong>\n' + escape(data.answer || '(no answer)') + srcHtml + meta + thumbs;
      slot.querySelectorAll('.nllm-thumbs button').forEach(function (b) {
        b.addEventListener('click', function () {
          feedback(qid, b.dataset.v);
          b.disabled = true; b.textContent = b.textContent + ' ✓';
        });
      });
    } catch (e) {
      slot.innerHTML = '<strong>Error:</strong> ' + escape(String(e));
    } finally {
      send.disabled = false;
    }
  });
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') send.click(); });
})();
</script>
<?php
echo html_writer::end_div();
echo $OUTPUT->footer();
