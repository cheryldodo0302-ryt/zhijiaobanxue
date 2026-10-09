import { watch, type Ref } from 'vue'
import { useAuthStore } from './stores/auth'
import { readWorkspace, writeWorkspace } from './workspace-storage'

// Caller supplies only navigation/filter fields. Never pass answers or secrets.
export function useCoursePreferences(name:string, courseId:Ref<string>, fields:Record<string,Ref<any>>) {
  const auth = useAuthStore()
  const defaults = Object.fromEntries(Object.entries(fields).map(([key,value])=>[key,value.value]))
  let restoring = false
  watch(courseId, id => {
    restoring = true
    const saved = readWorkspace<Record<string,unknown>>(auth.user?.user_id || '', `${name}:${id}`, {})
    for (const [key,field] of Object.entries(fields)) {
      const value = saved?.[key]
      field.value = value !== null && typeof value === typeof defaults[key] && !Array.isArray(value) && !Array.isArray(defaults[key])
        ? value : Array.isArray(defaults[key]) && Array.isArray(value) && value.every(x=>typeof x==='string')
          ? [...value] : defaults[key]
    }
    restoring = false
  }, {flush:'sync',immediate:true})
  watch(()=>Object.fromEntries(Object.entries(fields).map(([key,field])=>[key,field.value])), value => {
    if (!restoring && courseId.value) writeWorkspace(auth.user?.user_id || '', `${name}:${courseId.value}`, value)
  }, {deep:true,flush:'sync'})
}
