<?php

/**
 * @file
 * Fills each collection term's "Pager view" field from its page layout.
 *
 * Every Views block shown on the collection's page (its own Layout Builder
 * override, or the vocabulary default) becomes a pager source, in layout
 * order. Displays that don't describe the collection's own order are skipped.
 *
 * Run with:
 *   drush php:script web/modules/custom/nazilic_pager/scripts/populate-pager-sources.php
 *
 * Pass "--dry-run" after "--" to preview without saving:
 *   drush php:script web/modules/custom/nazilic_pager/scripts/populate-pager-sources.php -- --dry-run
 */

use Drupal\nazilic_pager\PagerSequence;

// Only run from the command line (drush php:script). If a web server ever
// executes this file directly, refuse before touching anything.
if (PHP_SAPI !== 'cli') {
  http_response_code(403);
  exit;
}

if (!class_exists('Drupal') || !\Drupal::hasContainer()) {
  fwrite(STDERR, "Run this through Drush: drush php:script " . __FILE__ . "\n");
  return;
}

if (!\Drupal::moduleHandler()->moduleExists('nazilic_pager')) {
  fwrite(STDERR, "The nazilic_pager module is not enabled. Run `drush config:import` (or `drush pm:enable nazilic_pager`) first.\n");
  return;
}

// Cross-collection or random displays: never a sensible order for one
// collection.
$skip = ['taxonomy_term:block_3', 'taxonomy_term:block_6'];
$dry_run = in_array('--dry-run', $extra ?? [], TRUE);

$default_sections = \Drupal::service('entity_display.repository')
  ->getViewDisplay('taxonomy_term', 'collections', 'default')
  ->getThirdPartySetting('layout_builder', 'sections', []);

$terms = \Drupal::entityTypeManager()->getStorage('taxonomy_term')
  ->loadByProperties(['vid' => 'collections']);

foreach ($terms as $term) {
  if (!$term->hasField(PagerSequence::TERM_FIELD)) {
    echo "Field " . PagerSequence::TERM_FIELD . " is missing; import config first.\n";
    return;
  }
  $override = $term->hasField('layout_builder__layout') ? $term->get('layout_builder__layout') : NULL;
  $sections = ($override && !$override->isEmpty()) ? $override->getSections() : $default_sections;

  $sources = [];
  foreach ($sections as $section) {
    $components = $section->getComponents();
    uasort($components, fn($a, $b) => $a->getWeight() <=> $b->getWeight());
    foreach ($components as $component) {
      if (preg_match('/^views_block:(.+)-(.+)$/', $component->getPluginId(), $m)) {
        $source = "$m[1]:$m[2]";
        if (!in_array($source, $skip, TRUE)) {
          $sources[$source] = $source;
        }
      }
    }
  }

  echo sprintf("%s %s => %s\n", $term->id(), $term->label(), implode(', ', $sources) ?: '(none)');
  if (!$dry_run) {
    $term->set(PagerSequence::TERM_FIELD, array_values($sources))->save();
  }
}
echo $dry_run ? "Dry run: nothing saved.\n" : "Saved.\n";
