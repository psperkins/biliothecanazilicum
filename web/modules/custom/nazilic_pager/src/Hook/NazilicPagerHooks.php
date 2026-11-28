<?php

declare(strict_types=1);

namespace Drupal\nazilic_pager\Hook;

use Drupal\Core\Hook\Attribute\Hook;

/**
 * Hook implementations for the Nazilic Pager module.
 */
class NazilicPagerHooks {

  /**
   * Implements hook_theme().
   */
  #[Hook('theme')]
  public function theme(): array {
    return [
      'nazilic_pager' => [
        'variables' => [
          'previous' => NULL,
          'next' => NULL,
          'previous_label' => NULL,
          'next_label' => NULL,
          'previous_class' => 'btn-info',
          'next_class' => 'btn-primary',
          'ends' => 'disable',
          'show_titles' => FALSE,
          'position' => NULL,
          'total' => NULL,
        ],
      ],
    ];
  }

}
