import type { ObjectDirective } from 'vue'

// Animate the existing pane, preserving form values, media and component state.
const running = new WeakMap<HTMLElement, Animation[]>()
function cancel(root: HTMLElement) {
  running.get(root)?.forEach(animation => animation.cancel())
  running.delete(root)
}
function reveal(root: HTMLElement, direction = 1) {
  cancel(root)
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  const pane = [...root.querySelectorAll<HTMLElement>('.el-tab-pane')]
    .find(element => getComputedStyle(element).display !== 'none')
  if (!pane || typeof pane.animate !== 'function') return
  const targets = pane.querySelector('.student-grid')?.children || pane.children
  running.set(root, [...targets].slice(0, 4).map((element, index) => element.animate(
    [{ opacity: 0, transform: `translateX(${direction * 18}px)` }, { opacity: 1, transform: 'translateX(0)' }],
    { duration: 280, delay: index * 30, easing: 'cubic-bezier(.16,1,.3,1)' },
  )))
}
export const vStudyMotion: ObjectDirective<HTMLElement, string> = {
  mounted(root) { reveal(root) },
  updated(root, binding) {
    if (binding.value === binding.oldValue) return
    const tabs = ['qa', 'materials', 'blocks', 'training', 'practice', 'profile', 'graph']
    const next = tabs.indexOf(binding.value.split(':').at(-1) || '')
    const previous = tabs.indexOf(binding.oldValue?.split(':').at(-1) || '')
    reveal(root, next < previous ? -1 : 1)
  },
  beforeUnmount: cancel,
}
