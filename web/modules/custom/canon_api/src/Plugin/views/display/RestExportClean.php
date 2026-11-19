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
   * Lazily created HTML to Markdown converter.
   */
  protected ?HtmlConverter $converter = NULL;

  /**
   * {@inheritdoc}
   */
  protected function defineOptions() {
    $options = parent::defineOptions();
    $options['markdown'] = ['default' => TRUE];
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
  }

  /**
   * {@inheritdoc}
   */
  public function submitOptionsForm(&$form, FormStateInterface $form_state) {
    parent::submitOptionsForm($form, $form_state);
    if ($form_state->get('section') === 'markdown') {
      $this->setOption('markdown', (bool) $form_state->getValue('markdown'));
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
