<?php

declare(strict_types=1);

namespace Drupal\canon_api\Plugin\views\display;

use Drupal\Core\Form\FormStateInterface;
use Drupal\Core\Render\RenderContext;
use Drupal\rest_export_nested\Plugin\views\display\RestExportNested;
use Drupal\views\Render\ViewsRenderPipelineMarkup;
use League\HTMLToMarkdown\HtmlConverter;

/**
 * REST export with nested JSON, readable characters and Markdown text.
 *
 * Builds on REST export nested (Views Field View output becomes real JSON
 * arrays) and additionally:
 * - encodes JSON without \uXXXX escaping of <, >, &, quotes and non-ASCII;
 * - converts HTML in string values to Markdown, so the output is suitable
 *   for language models and other non-HTML consumers.
 *
 * @ingroup views_display_plugins
 *
 * @ViewsDisplay(
 *   id = "rest_export_clean",
 *   title = @Translation("REST export (clean)"),
 *   help = @Translation("Nested JSON with readable characters and HTML converted to Markdown."),
 *   uses_route = TRUE,
 *   admin = @Translation("REST export (clean)"),
 *   returns_response = TRUE
 * )
 */
class RestExportClean extends RestExportNested {

  /**
   * Matches strings that contain an HTML tag or entity.
   */
  protected const HTML_PATTERN = '/<[a-z!\/][^>]*>|&(?:[a-z]+|#\d+|#x[0-9a-f]+);/i';

  /**
   * Separator that list fields must use for multiple values in the view.
   */
  public const LIST_SEPARATOR = '|';

  /**
   * Lazily created HTML to Markdown converter.
   */
  protected ?HtmlConverter $converter = NULL;

  /**
   * {@inheritdoc}
   */
  protected function defineOptions() {
    $options = parent::defineOptions();
    $options['markdown'] = ['default' => TRUE];
    $options['list_fields'] = ['default' => []];
    return $options;
  }

  /**
   * {@inheritdoc}
   */
  public function optionsSummary(&$categories, &$options) {
    parent::optionsSummary($categories, $options);
    $options['markdown'] = [
      'category' => 'path',
      'title' => $this->t('HTML to Markdown'),
      'value' => $this->getOption('markdown') ? $this->t('Yes') : $this->t('No'),
    ];
    $lists = $this->getOption('list_fields');
    $options['list_fields'] = [
      'category' => 'path',
      'title' => $this->t('List fields'),
      'value' => $lists ? implode(', ', $lists) : $this->t('None'),
    ];
  }

  /**
   * {@inheritdoc}
   */
  public function buildOptionsForm(&$form, FormStateInterface $form_state) {
    parent::buildOptionsForm($form, $form_state);
    if ($form_state->get('section') === 'markdown') {
      $form['#title'] .= $this->t('Convert HTML to Markdown');
      $form['markdown'] = [
        '#type' => 'checkbox',
        '#title' => $this->t('Convert HTML in field values to Markdown'),
        '#description' => $this->t('Applies to every string value, including nested views. Values without HTML are left unchanged.'),
        '#default_value' => $this->getOption('markdown'),
      ];
    }
    if ($form_state->get('section') === 'list_fields') {
      $form['#title'] .= $this->t('Fields output as JSON arrays');
      $form['list_fields'] = [
        '#type' => 'textfield',
        '#title' => $this->t('Field aliases'),
        '#description' => $this->t('Comma-separated row aliases to output as arrays. Set each field\'s multiple-value separator to "@sep" in the view.', ['@sep' => static::LIST_SEPARATOR]),
        '#default_value' => implode(', ', $this->getOption('list_fields')),
      ];
    }
  }

  /**
   * {@inheritdoc}
   */
  public function submitOptionsForm(&$form, FormStateInterface $form_state) {
    parent::submitOptionsForm($form, $form_state);
    if ($form_state->get('section') === 'markdown') {
      $this->setOption('markdown', (bool) $form_state->getValue('markdown'));
    }
    if ($form_state->get('section') === 'list_fields') {
      $aliases = array_filter(array_map('trim', explode(',', (string) $form_state->getValue('list_fields'))));
      $this->setOption('list_fields', array_values($aliases));
    }
  }

  /**
   * {@inheritdoc}
   */
  public function render() {
    $build = [];
    $markup = $this->renderer->executeInRenderContext(new RenderContext(), function () {
      return $this->view->style_plugin->render();
    });

    $results = json_decode((string) $markup, FALSE, 512, JSON_THROW_ON_ERROR);

    // Core's serializer returns an array of rows; custom styles may return an
    // object with a 'rows' property.
    if (is_object($results) && isset($results->rows)) {
      $results->rows = $this->flatten($results->rows);
    }
    elseif (is_array($results)) {
      $results = $this->flatten($results);
    }

    if ($lists = $this->getOption('list_fields')) {
      $rows = is_object($results) && isset($results->rows) ? $results->rows : $results;
      if (is_array($rows)) {
        foreach ($rows as $row) {
          $this->splitLists($row, $lists);
        }
      }
    }

    // Nested views are serialized as strings, so make their integer values
    // (IDs, revisions) numbers, matching the top-level rows.
    $results = $this->normalizeIntegers($results);

    if ($this->getOption('markdown')) {
      $results = $this->clean($results);
    }

    $build['#markup'] = json_encode($results, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);

    $this->view->element['#content_type'] = $this->getMimeType();
    $this->view->element['#cache_properties'][] = '#content_type';

    if (!empty($this->view->live_preview)) {
      $build['#prefix'] = '<pre>';
      $build['#plain_text'] = $build['#markup'];
      $build['#suffix'] = '</pre>';
      unset($build['#markup']);
    }
    elseif ($this->view->getRequest()->getFormat($this->view->element['#content_type']) !== 'html') {
      // Non-HTML response: mark as safe so the renderer does not run the HTML
      // XSS filter over JSON. See RestExport::render().
      $build['#markup'] = ViewsRenderPipelineMarkup::create($build['#markup']);
    }

    parent::applyDisplayCacheabilityMetadata($build);

    return $build;
  }

  /**
   * Turns the configured multi-value fields of a row into arrays.
   */
  protected function splitLists(object $row, array $aliases): void {
    foreach ($aliases as $alias) {
      if (!property_exists($row, $alias)) {
        continue;
      }
      $value = $row->$alias;
      if ($value === NULL || $value === '') {
        $row->$alias = [];
      }
      elseif (is_string($value)) {
        $row->$alias = array_values(array_filter(array_map('trim', explode(static::LIST_SEPARATOR, $value)), 'strlen'));
      }
      elseif (!is_array($value)) {
        $row->$alias = [$value];
      }
    }
  }

  /**
   * Recursively converts integer-only strings to integers.
   */
  protected function normalizeIntegers(mixed $data): mixed {
    if (is_string($data)) {
      return preg_match('/^(0|[1-9]\d{0,17})$/', $data) ? (int) $data : $data;
    }
    if (is_array($data)) {
      return array_map(fn($value) => $this->normalizeIntegers($value), $data);
    }
    if (is_object($data)) {
      foreach (get_object_vars($data) as $key => $value) {
        $data->$key = $this->normalizeIntegers($value);
      }
    }
    return $data;
  }

  /**
   * Recursively converts HTML strings in decoded JSON data to Markdown.
   */
  protected function clean(mixed $data): mixed {
    if (is_string($data)) {
      return preg_match(static::HTML_PATTERN, $data) ? $this->toMarkdown($data) : $data;
    }
    if (is_array($data)) {
      return array_map(fn($value) => $this->clean($value), $data);
    }
    if (is_object($data)) {
      foreach (get_object_vars($data) as $key => $value) {
        $data->$key = $this->clean($value);
      }
    }
    return $data;
  }

  /**
   * Converts an HTML fragment to tidy Markdown.
   */
  protected function toMarkdown(string $html): string {
    $this->converter ??= new HtmlConverter([
      'header_style' => 'atx',
      'hard_break' => TRUE,
      'strip_tags' => TRUE,
      'remove_nodes' => 'script style',
      'strip_placeholder_links' => TRUE,
    ]);

    $markdown = $this->converter->convert($html);

    // The converter re-escapes literal text such as "<instrumental>"; consumers
    // of this API read plain text, so decode it back.
    $markdown = html_entity_decode($markdown, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    // The converter also backslash-escapes literal Markdown punctuation, e.g.
    // a footnote "*" becomes "\*". Real formatting is never escaped, so drop
    // the backslashes to keep the text exactly as the author wrote it.
    $markdown = preg_replace('/\\\\([\\\\`*_{}\[\]()#+\-.!|<>~])/', '$1', $markdown);
    // Markdown autolinks (<https://...>) become plain URLs.
    $markdown = preg_replace('#<(https?://[^\s<>]+)>#', '$1', $markdown);
    // Non-breaking spaces become normal spaces.
    $markdown = str_replace("\u{00A0}", ' ', $markdown);
    // Drop headings with no text, e.g. from <h3>&nbsp;</h3>.
    $markdown = preg_replace('/^#{1,6}[ \t]*$/m', '', $markdown);
    // Trim trailing whitespace and collapse runs of blank lines.
    $markdown = preg_replace('/[ \t]+$/m', '', $markdown);
    $markdown = preg_replace("/\n{3,}/", "\n\n", $markdown);

    return trim($markdown);
  }

}
