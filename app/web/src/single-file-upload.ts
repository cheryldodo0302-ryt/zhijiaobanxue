import { computed, ref } from 'vue'
import { genFileId, type UploadRawFile, type UploadUserFile } from 'element-plus'

/** Keep the upload widget and the file submitted to the API in one state. */
export function useSingleFileUpload() {
  const files = ref<UploadUserFile[]>([])
  const file = computed(() => files.value[0]?.raw ?? null)

  function replace(selection: File[]) {
    const selected = selection[0]
    if (!selected) return
    const raw = selected as UploadRawFile
    raw.uid = genFileId()
    files.value = [{ name: raw.name, size: raw.size, uid: raw.uid, raw, status: 'ready' }]
  }

  function clear() {
    files.value = []
  }

  return { files, file, replace, clear }
}
