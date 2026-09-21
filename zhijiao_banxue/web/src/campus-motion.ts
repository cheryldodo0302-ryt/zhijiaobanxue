import type { ObjectDirective } from 'vue'

// Pointer depth belongs to the decorative art only. No reactive updates, scroll
// handlers, permanent animation loop, or input/card transforms are involved.
const cleanup = new WeakMap<HTMLElement, () => void>()
const headingSelector = '.dashboard-heading, .workbench-hero, .archive > .hero, .content > .page-title, .student-header'

export const vCampusDepth: ObjectDirective<HTMLElement> = {
  mounted(root) {
    const allowed = matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)')
    let active: HTMLElement | null = null
    let frame = 0
    const reset = () => {
      cancelAnimationFrame(frame)
      frame = 0
      active?.style.removeProperty('--art-x')
      active?.style.removeProperty('--art-y')
      active = null
    }
    const move = (event: PointerEvent) => {
      if (!allowed.matches || event.pointerType === 'touch') return reset()
      const next = event.target instanceof Element ? event.target.closest<HTMLElement>(headingSelector) : null
      if (next !== active) reset()
      if (!next || !root.contains(next)) return
      active = next
      cancelAnimationFrame(frame)
      const { clientX, clientY } = event
      frame = requestAnimationFrame(() => {
        if (!active) return
        const box = active.getBoundingClientRect()
        const x = Math.max(-1, Math.min(1, (clientX - box.left) / Math.max(box.width, 1) * 2 - 1))
        const y = Math.max(-1, Math.min(1, (clientY - box.top) / Math.max(box.height, 1) * 2 - 1))
        active.style.setProperty('--art-x', `${(x * 5).toFixed(2)}px`)
        active.style.setProperty('--art-y', `${(y * 3).toFixed(2)}px`)
        frame = 0
      })
    }
    root.addEventListener('pointermove', move, { passive: true })
    root.addEventListener('pointerleave', reset)
    allowed.addEventListener('change', reset)
    cleanup.set(root, () => {
      reset()
      root.removeEventListener('pointermove', move)
      root.removeEventListener('pointerleave', reset)
      allowed.removeEventListener('change', reset)
    })
  },
  beforeUnmount(root) { cleanup.get(root)?.(); cleanup.delete(root) },
}
