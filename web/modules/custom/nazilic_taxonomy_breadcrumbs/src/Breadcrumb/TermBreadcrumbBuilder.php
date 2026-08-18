<?php

namespace Drupal\nazilic_taxonomy_breadcrumbs\Breadcrumb;

use Drupal\Core\Breadcrumb\Breadcrumb;
use Drupal\Core\Breadcrumb\BreadcrumbBuilderInterface;
use Drupal\Core\Routing\RouteMatchInterface;
use Drupal\Core\Link;
use Drupal\Core\StringTranslation\StringTranslationTrait;
use Drupal\taxonomy\Entity\Term;
use Symfony\Component\DependencyInjection\ContainerInterface;
use Symfony\Component\HttpFoundation\RequestStack;
use Drupal\Core\Entity\EntityTypeManagerInterface;

/**
 * Provides a custom breadcrumb builder for taxonomy terms.
 */
class TermBreadcrumbBuilder implements BreadcrumbBuilderInterface {

  use StringTranslationTrait;

  /**
   * The request stack.
   *
   * @var \Symfony\Component\HttpFoundation\RequestStack
   */
  protected $requestStack;

  /**
   * The entity type manager.
   *
   * @var \Drupal\Core\Entity\EntityTypeManagerInterface
   */
  protected $entityTypeManager;

  /**
   * Constructs the TermBreadcrumbBuilder object.
   */
  public function __construct(RequestStack $request_stack, EntityTypeManagerInterface $entity_type_manager) {
    $this->requestStack = $request_stack;
    $this->entityTypeManager = $entity_type_manager;
  }

  /**
   * {@inheritdoc}
   */
  public static function create(ContainerInterface $container) {
    return new static(
      $container->get('request_stack'),
      $container->get('entity_type.manager')
    );
  }

  /**
   * {@inheritdoc}
   */
  public function applies(RouteMatchInterface $route_match) {
    return $route_match->getRouteName() === 'entity.taxonomy_term.canonical';
  }

  /**
   * {@inheritdoc}
   */
  public function build(RouteMatchInterface $route_match) {
    $breadcrumb = new Breadcrumb();
    $term = $route_match->getParameter('taxonomy_term');

    if (!$term instanceof Term) {
      return NULL;
    }

    // Add cache metadata.
    $breadcrumb->addCacheableDependency($term);
    $breadcrumb->addCacheContexts(['url']);

    // 1. Add Home link.
    $breadcrumb->addLink(Link::createFromRoute($this->t('Home'), '<front>'));

    // --- CUSTOM FIELD LOGIC START ---

    // Example A: Get a simple text field value (e.g., field_breadcrumb_label)
    // Replace 'field_breadcrumb_label' with your actual field machine name.


    // ... (Home and Parents logic) ...

    // Check if the custom field exists and has a value
    if ($term->hasField('field_collection') && !$term->get('field_collection')->isEmpty()) {
      $reference = $term->get('field_collection')->entity;
      if($reference) {
        $collection = $reference->label();

        // 2. Get the URL from the referenced entity
        $custom_url = $reference->toUrl();

        // Ensure cache depends on the referenced entity too!
        $breadcrumb->addCacheableDependency($collection);

        $display_label = !empty($collection) ? $collection : $term->label();
        $display_url = $custom_url ? $custom_url : $term->toUrl();

        // ... (Add parents logic here if needed) ...

        // Add the final link using the custom label and URL
        $breadcrumb->addLink(Link::fromTextAndUrl($display_label, $display_url));


        // If the field has a value, use it; otherwise, fall back to the term name.
        $display_label = !empty($ccollection) ? $custom_label : $term->label();
      }
    }


    // Example B: Get a value from an Entity Reference field (e.g., field_parent_page)
    // If you want to link to a specific node defined on the term instead of the term parent.
    /*
    $parent_node_ref = $term->get('field_parent_page')->entity;
    if ($parent_node_ref) {
      $breadcrumb->addLink(Link::fromTextAndUrl($parent_node_ref->label(), $parent_node_ref->toUrl()));
    }
    */

    // 2. Load all parents correctly.
    // loadAllParents() returns an array of parent terms from top-level to immediate parent.
    $storage = $this->entityTypeManager->getStorage('taxonomy_term');

    $parents = $storage->loadAllParents($term->id());

    // Remove the current term from the list if loadAllParents includes it (it usually doesn't, but safe to check).
    // loadAllParents typically returns ONLY ancestors.

    foreach ($parents as $parent) {
      // Ensure we don't link to the current term if it somehow slipped in,
      // and ensure the parent is accessible.
      if ($parent->id() !== $term->id()) {
        $breadcrumb->addLink(Link::fromTextAndUrl($parent->label(), $parent->toUrl()));
      }
    }

    $breadcrumb->addCacheContexts([
      'url.path',
      'languages:language_url',
      'languages:language_interface',
      'theme',
      'user.permissions',
      'url.path.parent',
      'url.path.is_front',
      'route'
    ]);

    // 3. Add the current term as the last item.
    $breadcrumb->addLink(Link::fromTextAndUrl($term->label(), $term->toUrl()));

    return $breadcrumb;
  }

}
