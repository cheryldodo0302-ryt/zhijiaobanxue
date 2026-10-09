import type { ObjectDirective } from 'vue'

const cleanup = new WeakMap<HTMLElement, () => void>()
let hoverLockUntil = 0

export const vCardHover: ObjectDirective<HTMLElement, () => void> = {
  mounted(el, binding) {
    let timer: ReturnType<typeof setTimeout> | undefined
    let selected = false
    const cancel = () => {
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
      el.classList.remove('card-hovered')
      el.style.removeProperty('--hover-rotate-x')
      el.style.removeProperty('--hover-rotate-y')
    }
    const schedule = () => {
      if (selected || performance.now() < hoverLockUntil || timer !== undefined) return
      timer = setTimeout(() => {
        timer = undefined
        if (!el.isConnected || performance.now() < hoverLockUntil) return
        selected = true
        hoverLockUntil = performance.now() + 760
        el.classList.remove('card-hovered')
        binding.value()
      }, 170)
    }
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return
      const rect = el.getBoundingClientRect()
      if (!rect.width || !rect.height) return
      const x = (event.clientX - rect.left) / rect.width
      const y = (event.clientY - rect.top) / rect.height
      el.style.setProperty('--hover-rotate-x', `${(0.5 - y) * 4}deg`)
      el.style.setProperty('--hover-rotate-y', `${(x - 0.5) * 5}deg`)
      el.classList.add('card-hovered')
      schedule()
    }
    const leave = cancel
    const click = () => { cancel(); hoverLockUntil = performance.now() + 760 }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerleave', leave)
    el.addEventListener('click', click)
    cleanup.set(el, () => {
      cancel()
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerleave', leave)
      el.removeEventListener('click', click)
    })
  },
  beforeUnmount(el) { cleanup.get(el)?.(); cleanup.delete(el) },
}
