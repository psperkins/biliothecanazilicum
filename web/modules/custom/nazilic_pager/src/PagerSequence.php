<?php

declare(strict_types=1);

namespace Drupal\nazilic_pager;

use Drupal\Core\Cache\CacheableMetadata;
use Drupal\Core\Entity\EntityTypeManagerInterface;
use Drupal\Core\Field\FieldStorageDefinitionInterface;
use Drupal\Core\Entity\FieldableEntityInterface;
use Drupal\views\ViewExecutableFactory;

/**
 * Builds ordered entity sequences by executing view displays.
 *
 * The view display is the single source of truth for ordering, filtering and
 * access: the pager only reads back the IDs of the rows it returns.
 */
class PagerSequence {

  /**
   * Taxonomy term field that lists the view displays for a collection.
   */
  public const TERM_FIELD = 'field_pager_view';

  /**
   * Display plugins that never make sense as a pager source.
   */
  protected const EXCLUDED_DISPLAY_PLUGINS = [
    'default',
    'feed',
    'rest_export',
    'rest_export_clean',
    'data_export',
    'entity_reference',
    'attachment',
  ];

  /**
   * Sequences already built during this request, keyed by view/display/args.
   *
   * @var array<string, array{entity_type: ?string, ids: string[], cache: CacheableMetadata}>
   */
  protected array $built = [];

  public function __construct(
    protected EntityTypeManagerInterface $entityTypeManager,
    protected ViewExecutableFactory $executableFactory,
  ) {}

  /**
   * Returns the ordered entity IDs a view display produces.
   *
   * @param string $source
   *   A "view_id:display_id" string.
   * @param array $args
   *   Contextual filter arguments for the display.
   * @param \Drupal\Core\Cache\CacheableMetadata $cache
   *   Collects the cacheability of the view and its results.
   *
   * @return array{entity_type: ?string, ids: string[]}
   *   The base entity type of the view and its de-duplicated row entity IDs,
   *   in display order. Empty if the source is invalid or not accessible.
   */
  public function getSequence(string $source, array $args, CacheableMetadata $cache): array {
    $empty = ['entity_type' => NULL, 'ids' => []];
    $key = $source . '|' . implode('/', $args);
    if (isset($this->built[$key])) {
      $cache->addCacheableDependency($this->built[$key]['cache']);
      return $this->built[$key];
    }

    [$view_id, $display_id] = array_pad(explode(':', $source, 2), 2, '');
    $storage = $view_id !== '' ? $this->entityTypeManager->getStorage('view')->load($view_id) : NULL;
    if (!$storage) {
      return $empty;
    }
    $own_cache = new CacheableMetadata();
    $own_cache->addCacheableDependency($storage);

    $view = $this->executableFactory->get($storage);
    if (!$view->setDisplay($display_id) || !$view->access($display_id)) {
      $own_cache->addCacheContexts(['user.permissions']);
      $cache->addCacheableDependency($own_cache);
      return $empty;
    }

    $view->setArguments(array_values($args));
    // A non-empty array stops views from reading exposed input from the
    // current request's query string.
    $view->setExposedInput(['nazilic_pager' => 1]);
    $view->setItemsPerPage(0);
    $view->setOffset(0);
    $view->preExecute();
    $view->execute();

    $own_cache->addCacheableDependency($view->display_handler->getCacheMetadata());
    $own_cache->addCacheTags($view->getCacheTags());

    $ids = [];
    $base_field = $storage->get('base_field');
    foreach ($view->result as $row) {
      $id = isset($row->_entity) ? $row->_entity->id() : ($row->{$base_field} ?? NULL);
      if ($id !== NULL) {
        // Rows can repeat when a relationship fans out (for example a node
        // with several tags). Keep the first occurrence only.
        $ids[(string) $id] = (string) $id;
      }
    }

    $result = [
      'entity_type' => $view->getBaseEntityType()?->id(),
      'ids' => array_values($ids),
      'cache' => $own_cache,
    ];
    $view->destroy();

    $this->built[$key] = $result;
    $cache->addCacheableDependency($own_cache);
    return $result;
  }

  /**
   * Lists view displays that can act as a pager source.
   *
   * @return array<string, string>
   *   Labels keyed by "view_id:display_id", sorted by label.
   */
  public function getSourceOptions(): array {
    $options = [];
    /** @var \Drupal\views\ViewEntityInterface $view */
    foreach ($this->entityTypeManager->getStorage('view')->loadMultiple() as $view) {
      if (!$view->status()) {
        continue;
      }
      foreach ($view->get('display') as $display_id => $display) {
        if (in_array($display['display_plugin'], self::EXCLUDED_DISPLAY_PLUGINS, TRUE)) {
          continue;
        }
        if ($display['display_options']['enabled'] ?? TRUE) {
          $options[$view->id() . ':' . $display_id] = sprintf('%s: %s (%s)', $view->label(), $display['display_title'], $display_id);
        }
      }
    }
    asort($options, SORT_NATURAL | SORT_FLAG_CASE);
    return $options;
  }

  /**
   * Allowed values callback for the field_pager_view list field.
   */
  public static function allowedValues(FieldStorageDefinitionInterface $definition, ?FieldableEntityInterface $entity = NULL, &$cacheable = TRUE): array {
    return \Drupal::service('nazilic_pager.sequence')->getSourceOptions();
  }

}
