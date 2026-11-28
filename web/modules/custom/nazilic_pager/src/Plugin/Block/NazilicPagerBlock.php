<?php

declare(strict_types=1);

namespace Drupal\nazilic_pager\Plugin\Block;

use Drupal\Core\Block\Attribute\Block;
use Drupal\Core\Block\BlockBase;
use Drupal\Core\Cache\Cache;
use Drupal\Core\Cache\CacheableMetadata;
use Drupal\Core\Entity\ContentEntityInterface;
use Drupal\Core\Entity\EntityTypeManagerInterface;
use Drupal\Core\Entity\FieldableEntityInterface;
use Drupal\Core\Field\EntityReferenceFieldItemListInterface;
use Drupal\Core\Form\FormStateInterface;
use Drupal\Core\Plugin\ContainerFactoryPluginInterface;
use Drupal\Core\Routing\RouteMatchInterface;
use Drupal\Core\StringTranslation\TranslatableMarkup;
use Drupal\nazilic_pager\PagerSequence;
use Symfony\Component\DependencyInjection\ContainerInterface;

/**
 * Previous/next links for the entity on the current page.
 *
 * The order comes from a view display, chosen either per collection term
 * (field_pager_view) or fixed on this block.
 */
#[Block(
  id: 'nazilic_pager',
  admin_label: new TranslatableMarkup('Nazilic pager'),
  category: new TranslatableMarkup('Nazilic'),
)]
class NazilicPagerBlock extends BlockBase implements ContainerFactoryPluginInterface {

  /**
   * Source mode: read the view displays from a referenced term.
   */
  public const SOURCE_TERM_FIELD = 'term_field';

  /**
   * Source mode: use the view display set on this block.
   */
  public const SOURCE_FIXED = 'fixed';

  /**
   * Fixed-mode argument value meaning "the current entity's own ID".
   */
  public const ARGUMENT_SELF = '@self';

  /**
   * End-of-sequence behaviour: show the missing button as disabled.
   */
  public const ENDS_DISABLE = 'disable';

  /**
   * End-of-sequence behaviour: leave the missing button out.
   */
  public const ENDS_HIDE = 'hide';

  public function __construct(
    array $configuration,
    $plugin_id,
    $plugin_definition,
    protected PagerSequence $sequence,
    protected EntityTypeManagerInterface $entityTypeManager,
    protected RouteMatchInterface $routeMatch,
  ) {
    parent::__construct($configuration, $plugin_id, $plugin_definition);
  }

  /**
   * {@inheritdoc}
   */
  public static function create(ContainerInterface $container, array $configuration, $plugin_id, $plugin_definition): static {
    return new static(
      $configuration,
      $plugin_id,
      $plugin_definition,
      $container->get('nazilic_pager.sequence'),
      $container->get('entity_type.manager'),
      $container->get('current_route_match'),
    );
  }

  /**
   * {@inheritdoc}
   */
  public function defaultConfiguration(): array {
    return [
      'source' => self::SOURCE_TERM_FIELD,
      'reference_field' => 'field_collection',
      'view_display' => '',
      'argument' => '',
      'previous_label' => 'Prev',
      'next_label' => 'Next',
      'previous_class' => 'btn-info',
      'next_class' => 'btn-primary',
      'ends' => self::ENDS_DISABLE,
      'show_titles' => FALSE,
    ];
  }

  /**
   * {@inheritdoc}
   */
  public function blockForm($form, FormStateInterface $form_state): array {
    $config = $this->configuration;
    $source_selector = ':input[name="settings[source]"]';

    $form['source'] = [
      '#type' => 'radios',
      '#title' => $this->t('Ordering source'),
      '#options' => [
        self::SOURCE_TERM_FIELD => $this->t('From the referenced term’s “Pager view” field'),
        self::SOURCE_FIXED => $this->t('A fixed view display'),
      ],
      '#default_value' => $config['source'],
      '#description' => $this->t('The pager runs the chosen view display with paging turned off and uses its row order. Unpublished or inaccessible items are left out because the view leaves them out.'),
    ];

    $form['reference_field'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Term reference field'),
      '#default_value' => $config['reference_field'],
      '#description' => $this->t('Machine name of the field on the current page’s entity that points at the term (for example <code>field_collection</code>). The term’s ID is passed to the view display as its first argument. If the term lists several displays, the first one that contains the current item is used.'),
      '#states' => [
        'visible' => [$source_selector => ['value' => self::SOURCE_TERM_FIELD]],
        'required' => [$source_selector => ['value' => self::SOURCE_TERM_FIELD]],
      ],
    ];

    $form['view_display'] = [
      '#type' => 'select',
      '#title' => $this->t('View display'),
      '#options' => $this->sequence->getSourceOptions(),
      '#empty_option' => $this->t('- Select -'),
      '#default_value' => $config['view_display'],
      '#states' => [
        'visible' => [$source_selector => ['value' => self::SOURCE_FIXED]],
        'required' => [$source_selector => ['value' => self::SOURCE_FIXED]],
      ],
    ];

    $form['argument'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Contextual filter argument'),
      '#default_value' => $config['argument'],
      '#description' => $this->t('Leave empty to pass no argument. Enter a field machine name (for example <code>field_vessel</code>) to pass the first ID it references, or <code>@self</code> to pass the current entity’s own ID.'),
      '#states' => [
        'visible' => [$source_selector => ['value' => self::SOURCE_FIXED]],
      ],
    ];

    $form['previous_label'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Previous label'),
      '#default_value' => $config['previous_label'],
    ];
    $form['next_label'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Next label'),
      '#default_value' => $config['next_label'],
    ];
    $form['previous_class'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Previous button classes'),
      '#default_value' => $config['previous_class'],
      '#description' => $this->t('Added after <code>btn</code>, for example <code>btn-info</code> or <code>btn-outline-secondary btn-sm</code>.'),
    ];
    $form['next_class'] = [
      '#type' => 'textfield',
      '#title' => $this->t('Next button classes'),
      '#default_value' => $config['next_class'],
      '#description' => $this->t('Added after <code>btn</code>, for example <code>btn-primary</code>.'),
    ];
    $form['ends'] = [
      '#type' => 'radios',
      '#title' => $this->t('At the first and last item'),
      '#options' => [
        self::ENDS_DISABLE => $this->t('Show the missing button as disabled'),
        self::ENDS_HIDE => $this->t('Hide the missing button'),
      ],
      '#default_value' => $config['ends'],
    ];
    $form['show_titles'] = [
      '#type' => 'checkbox',
      '#title' => $this->t('Show the titles of the previous and next items on the buttons'),
      '#default_value' => $config['show_titles'],
      '#description' => $this->t('When off, the titles are still available as a tooltip.'),
    ];

    return $form;
  }

  /**
   * {@inheritdoc}
   */
  public function blockValidate($form, FormStateInterface $form_state): void {
    $source = $form_state->getValue('source');
    if ($source === self::SOURCE_FIXED && !$form_state->getValue('view_display')) {
      $form_state->setErrorByName('view_display', $this->t('Choose a view display.'));
    }
    if ($source === self::SOURCE_TERM_FIELD && !trim((string) $form_state->getValue('reference_field'))) {
      $form_state->setErrorByName('reference_field', $this->t('Enter the term reference field.'));
    }
  }

  /**
   * {@inheritdoc}
   */
  public function blockSubmit($form, FormStateInterface $form_state): void {
    foreach (array_keys($this->defaultConfiguration()) as $key) {
      $value = $form_state->getValue($key);
      $this->configuration[$key] = is_string($value) ? trim($value) : $value;
    }
    $this->configuration['show_titles'] = (bool) $this->configuration['show_titles'];
  }

  /**
   * {@inheritdoc}
   */
  public function build(): array {
    $cache = new CacheableMetadata();
    $cache->addCacheContexts(['route']);
    $build = [];

    $entity = $this->getCurrentEntity();
    if ($entity) {
      $cache->addCacheableDependency($entity);
      $ids = $this->findSequence($entity, $cache);
      $position = $ids === NULL ? FALSE : array_search((string) $entity->id(), $ids, TRUE);
      if ($position !== FALSE) {
        $build = [
          '#theme' => 'nazilic_pager',
          '#previous' => $this->neighbour($entity->getEntityTypeId(), $ids[$position - 1] ?? NULL, $cache),
          '#next' => $this->neighbour($entity->getEntityTypeId(), $ids[$position + 1] ?? NULL, $cache),
          '#previous_label' => $this->configuration['previous_label'],
          '#next_label' => $this->configuration['next_label'],
          '#previous_class' => $this->configuration['previous_class'],
          '#next_class' => $this->configuration['next_class'],
          '#ends' => $this->configuration['ends'],
          '#show_titles' => $this->configuration['show_titles'],
          '#position' => $position + 1,
          '#total' => count($ids),
          '#attached' => ['library' => ['nazilic_pager/pager']],
        ];
        if (!$build['#previous'] && !$build['#next']) {
          $build = [];
        }
      }
    }

    $cache->applyTo($build);
    return $build;
  }

  /**
   * {@inheritdoc}
   */
  public function getCacheContexts(): array {
    return Cache::mergeContexts(parent::getCacheContexts(), ['route']);
  }

  /**
   * Returns the entity whose canonical page is being viewed, if any.
   */
  protected function getCurrentEntity(): ?ContentEntityInterface {
    if (!preg_match('/^entity\.([a-z0-9_]+)\.canonical$/', (string) $this->routeMatch->getRouteName(), $matches)) {
      return NULL;
    }
    $entity = $this->routeMatch->getParameter($matches[1]);
    return $entity instanceof ContentEntityInterface ? $entity : NULL;
  }

  /**
   * Finds the ordered ID list that contains the current entity.
   *
   * @return string[]|null
   *   The ordered IDs, or NULL when no configured source contains the entity.
   */
  protected function findSequence(ContentEntityInterface $entity, CacheableMetadata $cache): ?array {
    $candidates = [];

    if ($this->configuration['source'] === self::SOURCE_FIXED) {
      $args = $this->fixedArguments($entity);
      if ($args !== NULL && $this->configuration['view_display']) {
        $candidates[] = [$this->configuration['view_display'], $args];
      }
    }
    else {
      $field = $this->configuration['reference_field'];
      if ($entity instanceof FieldableEntityInterface && $entity->hasField($field) && $entity->get($field) instanceof EntityReferenceFieldItemListInterface) {
        foreach ($entity->get($field)->referencedEntities() as $term) {
          $cache->addCacheableDependency($term);
          if ($term instanceof FieldableEntityInterface && $term->hasField(PagerSequence::TERM_FIELD)) {
            foreach ($term->get(PagerSequence::TERM_FIELD) as $item) {
              $candidates[] = [$item->value, [$term->id()]];
            }
          }
        }
      }
    }

    foreach ($candidates as [$source, $args]) {
      $sequence = $this->sequence->getSequence($source, $args, $cache);
      if ($sequence['entity_type'] === $entity->getEntityTypeId() && in_array((string) $entity->id(), $sequence['ids'], TRUE)) {
        return $sequence['ids'];
      }
    }
    return NULL;
  }

  /**
   * Resolves the fixed-mode contextual filter arguments.
   *
   * @return array|null
   *   The arguments, or NULL if the configured field has no value.
   */
  protected function fixedArguments(ContentEntityInterface $entity): ?array {
    $argument = $this->configuration['argument'];
    if ($argument === '') {
      return [];
    }
    if ($argument === self::ARGUMENT_SELF) {
      return [$entity->id()];
    }
    if (!$entity->hasField($argument) || $entity->get($argument)->isEmpty()) {
      return NULL;
    }
    $item = $entity->get($argument)->first();
    $value = $item->target_id ?? $item->value ?? NULL;
    return $value === NULL ? NULL : [$value];
  }

  /**
   * Loads a neighbouring entity and returns its link data.
   */
  protected function neighbour(string $entity_type_id, ?string $id, CacheableMetadata $cache): ?array {
    if ($id === NULL) {
      return NULL;
    }
    $neighbour = $this->entityTypeManager->getStorage($entity_type_id)->load($id);
    if (!$neighbour) {
      return NULL;
    }
    $access = $neighbour->access('view', NULL, TRUE);
    $cache->addCacheableDependency($access);
    if (!$access->isAllowed()) {
      return NULL;
    }
    $cache->addCacheableDependency($neighbour);
    $url = $neighbour->toUrl()->toString(TRUE);
    $cache->addCacheableDependency($url);
    return [
      'url' => $url->getGeneratedUrl(),
      'title' => $neighbour->label(),
    ];
  }

}
