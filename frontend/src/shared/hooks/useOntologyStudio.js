import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const SESSION_ID = 'default'

const NEO4J_TOOLS = [
  'neo4j_cypher',
  'schema_create_class',
  'schema_create_relationship_type',
  'schema_get',
  'entity_create',
  'entity_search',
  'relationship_create',
]

const examples = [
  '업로드한 문서에서 엔티티를 추출해줘',
  '현재 스키마를 보여줘',
  'Neo4j에 저장된 엔티티들을 확인해줘',
]

export function useOntologyStudio() {
  const messages = ref([])
  const inputText = ref('')
  const isStreaming = ref(false)
  const uploadedFiles = ref([])
  const outputFiles = ref([])
  const todos = ref([])
  const skills = ref([])
  const refFiles = ref([])
  const panelOpen = reactive({
    todos: true,
    context: true,
    files: true,
    schema: true,
  })
  const neo4jConnected = ref(false)
  const schema = ref({ classes: [], relationships: [] })
  const entityCounts = ref([])

  let neo4jInterval = null

  function isNeo4jTool(name) {
    return NEO4J_TOOLS.includes(name)
  }

  function scrollBottom() {
    nextTick(() => {
      const chat = document.querySelector('.chat-messages')
      if (chat) {
        chat.scrollTop = chat.scrollHeight
      }
    })
  }

  async function checkNeo4jStatus() {
    try {
      const res = await fetch(`${API}/api/neo4j/status`)
      const data = await res.json()
      neo4jConnected.value = data.status === 'connected'
    } catch {
      neo4jConnected.value = false
    }
  }

  async function refreshEntityCounts() {
    if (!schema.value.classes.length) {
      entityCounts.value = []
      return
    }

    try {
      const res = await fetch(`${API}/api/graph?limit=0`)
      const data = await res.json()
      const counts = {}
      for (const node of data.nodes || []) {
        for (const label of node.labels || []) {
          if (label !== '_Entity') {
            counts[label] = (counts[label] || 0) + 1
          }
        }
      }
      entityCounts.value = Object.entries(counts).map(([name, count]) => ({ name, count }))
    } catch {
      entityCounts.value = []
    }
  }

  async function refreshSchema() {
    try {
      const res = await fetch(`${API}/api/schema`)
      const data = await res.json()
      schema.value = {
        classes: data.classes || [],
        relationships: data.relationships || [],
      }
      await refreshEntityCounts()
    } catch {
      // ignore
    }
  }

  async function refreshFiles() {
    try {
      const res = await fetch(`${API}/api/files`)
      const data = await res.json()
      uploadedFiles.value = data.uploads || []
      outputFiles.value = data.output || []
    } catch {
      // ignore
    }
  }

  async function refreshAll() {
    await Promise.all([refreshFiles(), checkNeo4jStatus(), refreshSchema()])
  }

  async function uploadFiles(fileList) {
    if (!fileList?.length) {
      return []
    }

    const fd = new FormData()
    const names = []
    for (const file of fileList) {
      fd.append('files', file)
      names.push(file.name)
    }
    fd.append('session_id', SESSION_ID)
    await fetch(`${API}/api/upload`, { method: 'POST', body: fd })
    await refreshFiles()
    return names
  }

  async function doUploadAndNotify(fileList) {
    const names = await uploadFiles(fileList)
    if (names.length) {
      messages.value.push({
        role: 'user',
        text: `파일 업로드: ${names.join(', ')}`,
        files: names,
      })
      scrollBottom()
    }
  }

  function downloadFile(name) {
    window.open(`${API}/api/download/${encodeURIComponent(name)}`, '_blank')
  }

  async function send(textOverride = null) {
    const text = (textOverride ?? inputText.value).trim()
    if (!text || isStreaming.value) {
      return
    }

    messages.value.push({ role: 'user', text })
    inputText.value = ''
    scrollBottom()

    messages.value.push({ role: 'assistant', steps: [], files: [] })
    isStreaming.value = true
    const getAiMsg = () => messages.value[messages.value.length - 1]
    let currentTokenIdx = -1

    const url = `${API}/api/stream?prompt=${encodeURIComponent(text)}&session_id=${SESSION_ID}`
    const es = new EventSource(url)

    es.addEventListener('token', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()
      if (currentTokenIdx >= 0 && aiMsg.steps[currentTokenIdx]?.type === 'token') {
        aiMsg.steps[currentTokenIdx].text += data.text
      } else {
        currentTokenIdx = aiMsg.steps.length
        aiMsg.steps.push({ type: 'token', text: data.text })
      }
      scrollBottom()
    })

    es.addEventListener('tool_start', (event) => {
      const data = JSON.parse(event.data)
      currentTokenIdx = -1
      getAiMsg().steps.push({ type: 'tool_start', name: data.name, done: false })
      scrollBottom()
    })

    es.addEventListener('tool_result', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()
      for (let index = aiMsg.steps.length - 1; index >= 0; index -= 1) {
        if (aiMsg.steps[index].type === 'tool_start' && !aiMsg.steps[index].done) {
          aiMsg.steps[index].done = true
          break
        }
      }
      aiMsg.steps.push({ type: 'tool_result', name: data.name, content: data.content })
      currentTokenIdx = -1
      scrollBottom()
    })

    es.addEventListener('neo4j_update', () => {
      refreshSchema()
    })

    es.addEventListener('todos', (event) => {
      const data = JSON.parse(event.data)
      todos.value = data.items || []
    })

    es.addEventListener('skill_loaded', (event) => {
      const data = JSON.parse(event.data)
      if (!skills.value.includes(data.name)) {
        skills.value.push(data.name)
      }
    })

    es.addEventListener('ref_file', (event) => {
      const data = JSON.parse(event.data)
      if (!refFiles.value.includes(data.name)) {
        refFiles.value.push(data.name)
      }
    })

    es.addEventListener('status', scrollBottom)
    es.addEventListener('node_update', scrollBottom)

    es.addEventListener('done', (event) => {
      const data = JSON.parse(event.data)
      if (data.files?.length) {
        getAiMsg().files = data.files
      }
      isStreaming.value = false
      es.close()
      refreshAll()
      scrollBottom()
    })

    es.addEventListener('error_event', (event) => {
      const data = JSON.parse(event.data)
      getAiMsg().steps.push({ type: 'token', text: `\n오류: ${data.message}` })
      isStreaming.value = false
      es.close()
      scrollBottom()
    })

    es.onerror = () => {
      isStreaming.value = false
      es.close()
      scrollBottom()
    }
  }

  async function resetSession() {
    await fetch(`${API}/api/session/reset`, { method: 'POST' })
    messages.value = []
    uploadedFiles.value = []
    outputFiles.value = []
    todos.value = []
    skills.value = []
    refFiles.value = []
    await refreshSchema()
  }

  onMounted(() => {
    refreshAll()
    neo4jInterval = setInterval(checkNeo4jStatus, 30000)
  })

  onUnmounted(() => {
    if (neo4jInterval) {
      clearInterval(neo4jInterval)
    }
  })

  return {
    entityCounts,
    examples,
    inputText,
    isNeo4jTool,
    isStreaming,
    messages,
    neo4jConnected,
    outputFiles,
    panelOpen,
    refFiles,
    refreshAll,
    resetSession,
    schema,
    send,
    skills,
    todos,
    uploadedFiles,
    doUploadAndNotify,
    downloadFile,
  }
}
