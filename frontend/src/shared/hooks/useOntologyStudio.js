import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const SESSION_ID = 'default'
const DEFAULT_BUILD_PROMPT = '업로드된 문서를 바탕으로 Golden Question에 답할 수 있는 온톨로지를 구축해줘.'
const DEFAULT_REVIEW_PROMPT = '방금 검토 피드백을 반영해서 Golden Question에 더 잘 답할 수 있도록 온톨로지를 개선해줘.'

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
    inputPlaceholder: '추가 지시사항이 있으면 입력하세요. 비워두면 설정한 의도와 Golden Question 기준으로 바로 구축합니다.',
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

function createBuildBrief() {
  return {
    intent: '',
    goldenQuestions: [''],
  }
}

function createModeState(targetMode) {
  return {
    draft: '',
    messages: [],
    todos: [],
    skills: [],
    refFiles: [],
    buildBrief: targetMode === 'build' ? createBuildBrief() : null,
  }
}

function normalizeGoldenQuestions(items = []) {
  return items.map((item) => item.trim()).filter(Boolean)
}

function createReviewItem(item) {
  return {
    question: item.question || '',
    answer: item.answer || '',
    status: item.status || 'answerable',
    confidence: item.confidence || 'medium',
    verdict: null,
    feedback: '',
  }
}

function createBuildReport(report) {
  return {
    intent: report.intent || '',
    summary: report.summary || '',
    nextAction: report.next_action || report.nextAction || '',
    goldenQuestions: Array.isArray(report.golden_questions)
      ? report.golden_questions.map(createReviewItem)
      : [],
  }
}

export function useOntologyStudio() {
  const mode = ref('build')
  const modeOptions = Object.values(MODES)
  const modeState = reactive({
    build: createModeState('build'),
    answer: createModeState('answer'),
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
  const buildBrief = computed(() => modeState.build.buildBrief)
  const buildIntent = computed({
    get: () => buildBrief.value.intent,
    set: (value) => {
      buildBrief.value.intent = value
    },
  })
  const buildGoldenQuestions = computed(() => buildBrief.value.goldenQuestions)
  const normalizedBuildGoldenQuestions = computed(() =>
    normalizeGoldenQuestions(buildBrief.value.goldenQuestions)
  )
  const isBuildBriefReady = computed(
    () =>
      Boolean(buildIntent.value.trim()) &&
      normalizedBuildGoldenQuestions.value.length > 0 &&
      uploadedFiles.value.length > 0
  )
  const canSend = computed(() => {
    if (isStreaming.value) {
      return false
    }
    if (mode.value === 'build') {
      return isBuildBriefReady.value
    }
    return Boolean(inputText.value.trim())
  })
  const effectiveInputPlaceholder = computed(() => {
    if (mode.value === 'build' && !isBuildBriefReady.value) {
      return '먼저 파일을 업로드하고, 구축 의도와 최소 1개의 Golden Question을 입력하세요...'
    }
    return currentModeMeta.value.inputPlaceholder
  })

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
    if (state.buildBrief) {
      state.buildBrief.intent = ''
      replaceItems(state.buildBrief.goldenQuestions, [''])
    }
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

  function setBuildGoldenQuestion(index, value) {
    if (buildBrief.value.goldenQuestions[index] === undefined) {
      return
    }
    buildBrief.value.goldenQuestions[index] = value
  }

  function addBuildGoldenQuestion() {
    buildBrief.value.goldenQuestions.push('')
  }

  function importBuildGoldenQuestions(questions) {
    const existing = buildBrief.value.goldenQuestions
    const hasOnlyEmptySlot = existing.length === 1 && !existing[0].trim()
    if (hasOnlyEmptySlot) {
      buildBrief.value.goldenQuestions = [...questions]
    } else {
      buildBrief.value.goldenQuestions.push(...questions)
    }
  }

  function removeBuildGoldenQuestion(index) {
    if (buildBrief.value.goldenQuestions.length === 1) {
      buildBrief.value.goldenQuestions[0] = ''
      return
    }
    buildBrief.value.goldenQuestions.splice(index, 1)
  }

  function createBuildContext(reviewFeedback = []) {
    return {
      intent: buildIntent.value.trim(),
      golden_questions: [...normalizedBuildGoldenQuestions.value],
      review_feedback: reviewFeedback,
    }
  }

  function replaceAssistantText(message, text) {
    const nextSteps = []
    let inserted = false
    for (const step of message.steps) {
      if (step.type === 'token') {
        if (!inserted && text.trim()) {
          nextSteps.push({ type: 'token', text })
          inserted = true
        }
        continue
      }
      nextSteps.push(step)
    }
    if (!inserted && text.trim()) {
      nextSteps.push({ type: 'token', text })
    }
    replaceItems(message.steps, nextSteps)
  }

  function updateAssistantPreprocessState(message, data = {}) {
    message.preprocessState = {
      ...(message.preprocessState || {}),
      ...data,
    }
  }

  function normalizeReportFeedback(message) {
    const report = message?.buildReport
    if (!report) {
      return []
    }

    return report.goldenQuestions
      .filter((item) => item.verdict)
      .map((item) => ({
        question: item.question,
        answer: item.answer,
        status: item.status,
        verdict: item.verdict,
        feedback: item.feedback.trim(),
      }))
  }

  function canSubmitBuildFeedback(message) {
    const reviewFeedback = normalizeReportFeedback(message)
    const incorrectItems = reviewFeedback.filter((item) => item.verdict === 'incorrect')
    return incorrectItems.length > 0 && incorrectItems.every((item) => item.feedback)
  }

  function buildStreamUrl(prompt, currentMode, buildContext = null) {
    const url = new URL(`${API}/api/stream`)
    url.searchParams.set('prompt', prompt)
    url.searchParams.set('session_id', SESSION_ID)
    url.searchParams.set('mode', currentMode)
    if (buildContext) {
      url.searchParams.set('build_context', JSON.stringify(buildContext))
    }
    return url.toString()
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
    const shouldNotifyInChat = mode.value !== 'build' || getModeState().messages.length > 0
    if (names.length && shouldNotifyInChat) {
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

  function startStreamingRequest({ currentMode, prompt, userMessage, buildContext = null }) {
    const state = getModeState(currentMode)

    state.messages.push(userMessage)
    state.draft = ''
    scrollBottom()

    state.messages.push({ role: 'assistant', steps: [], files: [], mode: currentMode })
    isStreaming.value = true

    const getAiMsg = () => {
      const currentMessages = getModeState(currentMode).messages
      return currentMessages[currentMessages.length - 1]
    }

    let currentTokenIdx = -1
    const es = new EventSource(buildStreamUrl(prompt, currentMode, buildContext))

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

    es.addEventListener('preprocess_progress', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()
      updateAssistantPreprocessState(aiMsg, data)
      scrollBottom()
    })

    es.addEventListener('preprocess_complete', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()
      aiMsg.preprocessSummary = data.summary || null
      updateAssistantPreprocessState(aiMsg, {
        completed: true,
        progress: 100,
      })
      scrollBottom()
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

    es.addEventListener('status', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()
      aiMsg.statusMessage = data.message || ''
      scrollBottom()
    })
    es.addEventListener('node_update', scrollBottom)

    es.addEventListener('done', (event) => {
      const data = JSON.parse(event.data)
      const aiMsg = getAiMsg()

      if (typeof data.text === 'string') {
        replaceAssistantText(aiMsg, data.text)
      }
      if (data.build_report) {
        aiMsg.buildReport = createBuildReport(data.build_report)
      }
      if (data.files?.length) {
        aiMsg.files = data.files
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

  async function send(textOverride = null) {
    const currentMode = mode.value
    const state = getModeState(currentMode)
    const rawText = (textOverride ?? state.draft).trim()

    if (isStreaming.value) {
      return
    }

    if (currentMode === 'build' && !isBuildBriefReady.value) {
      return
    }

    const prompt = currentMode === 'build' ? rawText || DEFAULT_BUILD_PROMPT : rawText
    if (!prompt) {
      return
    }

    const userMessage =
      currentMode === 'build'
        ? {
            role: 'user',
            text: rawText || '구축 의도와 Golden Question을 기준으로 온톨로지 구축 시작',
            mode: currentMode,
            buildBrief: {
              intent: buildIntent.value.trim(),
              goldenQuestions: [...normalizedBuildGoldenQuestions.value],
            },
          }
        : {
            role: 'user',
            text: prompt,
            mode: currentMode,
          }

    startStreamingRequest({
      currentMode,
      prompt,
      userMessage,
      buildContext: currentMode === 'build' ? createBuildContext() : null,
    })
  }

  async function submitBuildFeedback(message) {
    if (isStreaming.value || !canSubmitBuildFeedback(message)) {
      return
    }

    const reviewFeedback = normalizeReportFeedback(message)
    startStreamingRequest({
      currentMode: 'build',
      prompt: DEFAULT_REVIEW_PROMPT,
      userMessage: {
        role: 'user',
        text: 'Golden Question 검토 피드백 반영 요청',
        mode: 'build',
        buildFeedback: reviewFeedback,
      },
      buildContext: createBuildContext(reviewFeedback),
    })
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
    addBuildGoldenQuestion,
    importBuildGoldenQuestions,
    buildGoldenQuestions,
    buildIntent,
    canSend,
    canSubmitBuildFeedback,
    currentModeMeta,
    doUploadAndNotify,
    downloadFile,
    effectiveInputPlaceholder,
    entityCounts,
    examples,
    inputText,
    isBuildBriefReady,
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
    removeBuildGoldenQuestion,
    resetSession,
    schema,
    send,
    setBuildGoldenQuestion,
    setMode,
    showFilePanel,
    skills,
    submitBuildFeedback,
    todos,
    uploadedFiles,
  }
}
