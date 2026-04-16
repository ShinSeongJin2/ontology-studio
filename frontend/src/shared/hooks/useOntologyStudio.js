import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const SESSION_ID = 'default'

const NEO4J_TOOLS = [
  'neo4j_cypher',
  'neo4j_cypher_readonly',
  'schema_create_class',
  'schema_create_relationship_type',
  'schema_get',
  'entity_create',
  'entity_search',
  'relationship_create',
]

const MODES = {
  build: {
    key: 'build',
    label: '온톨로지 구축',
    description: '문서를 분석하고 스키마, 엔티티, 관계를 생성하는 작업 모드입니다.',
    inputPlaceholder: '문서 기반 온톨로지 구축 작업을 요청하세요...',
    allowsFiles: true,
    examples: [
      '업로드한 문서에서 엔티티를 추출해줘',
      '현재 스키마를 보여줘',
      '문서 내용을 바탕으로 관계 유형을 제안해줘',
    ],
  },
  answer: {
    key: 'answer',
    label: '질문 응답',
    description: '이미 구축된 온톨로지를 조회해 구체적인 질문에 답변하는 모드입니다.',
    inputPlaceholder: '구축된 온톨로지에 대한 질문을 입력하세요...',
    allowsFiles: false,
    examples: [
      '현재 그래프에 어떤 클래스와 관계가 정의되어 있어?',
      'Company 클래스 엔티티를 찾아줘',
      'Person과 Company 사이에 어떤 관계가 있는지 설명해줘',
    ],
  },
}

const MODE_KEYS = Object.keys(MODES)

function createModeState() {
  return {
    draft: '',
    messages: [],
    todos: [],
    skills: [],
    refFiles: [],
  }
}

export function useOntologyStudio() {
  const mode = ref('build')
  const modeOptions = Object.values(MODES)
  const modeState = reactive({
    build: createModeState(),
    answer: createModeState(),
  })
  const isStreaming = ref(false)
  const uploadedFiles = ref([])
  const outputFiles = ref([])
  const panelOpen = reactive({
    todos: true,
    context: true,
    files: true,
    schema: true,
  })
  const neo4jConnected = ref(false)
  const schema = ref({ classes: [], relationships: [] })
  const entityCounts = ref([])
  const currentModeMeta = computed(() => MODES[mode.value])
  const inputText = computed({
    get: () => modeState[mode.value].draft,
    set: (value) => {
      modeState[mode.value].draft = value
    },
  })
  const messages = computed(() => modeState[mode.value].messages)
  const todos = computed(() => modeState[mode.value].todos)
  const skills = computed(() => modeState[mode.value].skills)
  const refFiles = computed(() => modeState[mode.value].refFiles)
  const examples = computed(() => currentModeMeta.value.examples)
  const showFilePanel = computed(() => currentModeMeta.value.allowsFiles)

  let neo4jInterval = null

  function getModeState(targetMode = mode.value) {
    return modeState[targetMode]
  }

  function replaceItems(target, items) {
    target.splice(0, target.length, ...items)
  }

  function resetModeState(targetMode) {
    const state = getModeState(targetMode)
    state.draft = ''
    replaceItems(state.messages, [])
    replaceItems(state.todos, [])
    replaceItems(state.skills, [])
    replaceItems(state.refFiles, [])
  }

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

  function setMode(nextMode) {
    if (!MODES[nextMode] || nextMode === mode.value || isStreaming.value) {
      return
    }
    mode.value = nextMode
    scrollBottom()
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
    if (!currentModeMeta.value.allowsFiles) {
      return
    }

    const names = await uploadFiles(fileList)
    if (names.length) {
      getModeState().messages.push({
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
    const currentMode = mode.value
    const state = getModeState(currentMode)
    const text = (textOverride ?? state.draft).trim()
    if (!text || isStreaming.value) {
      return
    }

    state.messages.push({ role: 'user', text, mode: currentMode })
    state.draft = ''
    scrollBottom()

    state.messages.push({ role: 'assistant', steps: [], files: [], mode: currentMode })
    isStreaming.value = true
    const getAiMsg = () => {
      const currentMessages = getModeState(currentMode).messages
      return currentMessages[currentMessages.length - 1]
    }
    let currentTokenIdx = -1

    const url = `${API}/api/stream?prompt=${encodeURIComponent(text)}&session_id=${SESSION_ID}&mode=${currentMode}`
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
      replaceItems(getModeState(currentMode).todos, data.items || [])
    })

    es.addEventListener('skill_loaded', (event) => {
      const data = JSON.parse(event.data)
      const currentSkills = getModeState(currentMode).skills
      if (!currentSkills.includes(data.name)) {
        currentSkills.push(data.name)
      }
    })

    es.addEventListener('ref_file', (event) => {
      const data = JSON.parse(event.data)
      const currentRefFiles = getModeState(currentMode).refFiles
      if (!currentRefFiles.includes(data.name)) {
        currentRefFiles.push(data.name)
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
    uploadedFiles.value = []
    outputFiles.value = []
    for (const key of MODE_KEYS) {
      resetModeState(key)
    }
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
    currentModeMeta,
    entityCounts,
    examples,
    inputText,
    isNeo4jTool,
    isStreaming,
    messages,
    mode,
    modeOptions,
    neo4jConnected,
    outputFiles,
    panelOpen,
    refFiles,
    refreshAll,
    resetSession,
    schema,
    send,
    setMode,
    showFilePanel,
    skills,
    todos,
    uploadedFiles,
    doUploadAndNotify,
    downloadFile,
  }
}
