<template>
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>Ontology Studio</h2>
        <div class="header-actions">
          <Neo4jStatusBadge :connected="neo4jConnected" />
          <button class="btn-icon" title="새로고침" @click="refreshAll">&#x21bb;</button>
        </div>
      </div>

      <div class="sidebar-scroll">
        <section class="mode-section">
          <button
            class="mode-section-btn"
            :class="{ active: mode === 'build' }"
            :disabled="isStreaming"
            @click="setMode('build')"
          >
            <span class="mode-switcher-btn-label">온톨로지 구축</span>
            <span class="mode-switcher-btn-desc">문서를 분석하고 스키마, 엔티티, 관계를 생성하는 작업 모드입니다.</span>
          </button>
          <ul v-if="buildSessions.length" class="session-list-inline">
            <li
              v-for="s in buildSessions"
              :key="s.id"
              class="session-item"
              :class="{ active: s.id === sessionId }"
              @click="switchSession(s.id)"
            >
              <span class="session-title">{{ s.title || '새 대화' }}</span>
              <span class="session-date">{{ formatSessionDate(s.updated_at) }}</span>
              <button class="session-delete" title="삭제" @click.stop="deleteSession(s.id)">&times;</button>
            </li>
          </ul>
        </section>

        <section class="mode-section">
          <button
            class="mode-section-btn"
            :class="{ active: mode === 'answer' }"
            :disabled="isStreaming"
            @click="setMode('answer')"
          >
            <span class="mode-switcher-btn-label">질문 응답</span>
            <span class="mode-switcher-btn-desc">이미 구축된 온톨로지를 조회해 구체적인 질문에 답변하는 모드입니다.</span>
          </button>
          <ul v-if="answerSessions.length" class="session-list-inline">
            <li
              v-for="s in answerSessions"
              :key="s.id"
              class="session-item"
              :class="{ active: s.id === sessionId }"
              @click="switchSession(s.id)"
            >
              <span class="session-title">{{ s.title || '새 대화' }}</span>
              <span class="session-date">{{ formatSessionDate(s.updated_at) }}</span>
              <button class="session-delete" title="삭제" @click.stop="deleteSession(s.id)">&times;</button>
            </li>
          </ul>
        </section>
      </div>

      <div class="sidebar-footer">
        <button class="btn-reset btn-danger" @click="confirmClearNeo4j">Neo4j 전체 초기화</button>
      </div>
    </aside>

    <main class="workspace-main">
      <OntologyBuildBriefPanel
        v-if="mode === 'build' && messages.length === 0"
        :can-start="isBuildBriefReady"
        :golden-questions="buildGoldenQuestions"
        :intent="buildIntent"
        :is-streaming="isStreaming"
        :uploaded-files="uploadedFiles"
        @add-question="addBuildGoldenQuestion"
        @import-questions="importBuildGoldenQuestions"
        @remove-question="removeBuildGoldenQuestion"
        @start-build="send"
        @update:intent="buildIntent = $event"
        @update:question="setBuildGoldenQuestion"
        @upload="doUploadAndNotify"
      />

      <ChatPanel
        v-else
        :can-send="canSend"
        :can-submit-build-feedback="canSubmitBuildFeedback"
        v-model:input-text="inputText"
        :examples="examples"
        :input-placeholder="effectiveInputPlaceholder"
        :is-neo4j-tool="isNeo4jTool"
        :is-streaming="isStreaming"
        :messages="messages"
        :mode-description="currentModeMeta.description"
        :mode-label="currentModeMeta.label"
        @download="downloadFile"
        @send="send"
        @send-example="send"
        @stop="stopStreaming"
        @submit-build-feedback="submitBuildFeedback"
      />
    </main>

    <div
      class="right-panel-resize-handle"
      @mousedown="startResize"
      @dblclick="toggleExpand"
    ></div>
    <aside class="right-panel" :style="{ width: rightPanelWidth + 'px' }">
      <div class="right-panel-header">
        <h3>온톨로지 그래프</h3>
        <div class="right-panel-tabs">
          <button
            class="right-tab-btn"
            :class="{ active: rightTab === 'graph' }"
            @click="rightTab = 'graph'"
          >그래프</button>
          <button
            class="right-tab-btn"
            :class="{ active: rightTab === 'info' }"
            @click="rightTab = 'info'"
          >정보</button>
        </div>
      </div>
      <div v-if="rightTab === 'graph'" class="right-panel-graph">
        <OntologyGraphPanel
          :api-base="apiBase"
          :entity-counts="entityCounts"
          :graph-data="graphData"
          :schema="schema"
          :traversed-node-ids="traversedNodeIds"
          @filter="handleGraphFilter"
          @refresh="fetchGraphData"
        />
      </div>
      <div v-else class="right-panel-scroll">
        <TodoPanel
          :open="panelOpen.todos"
          :todos="todos"
          @toggle="panelOpen.todos = !panelOpen.todos"
        />
        <OntologySchemaPanel
          :entity-counts="entityCounts"
          :open="panelOpen.schema"
          :schema="schema"
          @toggle="panelOpen.schema = !panelOpen.schema"
        />
        <ContextPanel
          :open="panelOpen.context"
          :ref-files="refFiles"
          :skills="skills"
          @toggle="panelOpen.context = !panelOpen.context"
        />
      </div>
    </aside>
  </div>
</template>

<script setup>
import ChatPanel from '../features/chat/ChatPanel.vue'
import OntologyBuildBriefPanel from '../features/ontology/OntologyBuildBriefPanel.vue'
import OntologyGraphPanel from '../features/ontology/OntologyGraphPanel.vue'
import OntologySchemaPanel from '../features/ontology/OntologySchemaPanel.vue'
import ContextPanel from '../features/session/ContextPanel.vue'
import TodoPanel from '../features/session/TodoPanel.vue'
import { ref, onUnmounted } from 'vue'
import { useOntologyStudio } from '../shared/hooks/useOntologyStudio.js'
import Neo4jStatusBadge from '../shared/ui/Neo4jStatusBadge.vue'

const {
  apiBase,
  addBuildGoldenQuestion,
  answerSessions,
  buildSessions,
  importBuildGoldenQuestions,
  buildGoldenQuestions,
  buildIntent,
  canSend,
  canSubmitBuildFeedback,
  clearNeo4jAll,
  createNewSession,
  currentModeMeta,
  deleteSession,
  doUploadAndNotify,
  downloadFile,
  effectiveInputPlaceholder,
  entityCounts,
  examples,
  fetchGraphData,
  graphData,
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
  sessionId,
  sessions,
  setBuildGoldenQuestion,
  setMode,
  showFilePanel,
  skills,
  stopStreaming,
  submitBuildFeedback,
  switchSession,
  todos,
  traversedNodeIds,
  uploadedFiles,
} = useOntologyStudio()

const rightTab = ref('graph')
const rightPanelWidth = ref(380)
const DEFAULT_PANEL_WIDTH = 380
const MIN_PANEL_WIDTH = 280
const MAX_PANEL_WIDTH = 900
const EXPANDED_RATIO = 0.55
let isResizing = false
let widthBeforeExpand = null

function startResize(e) {
  isResizing = true
  const startX = e.clientX
  const startWidth = rightPanelWidth.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'

  function onMouseMove(e) {
    if (!isResizing) return
    const delta = startX - e.clientX
    const newWidth = Math.min(MAX_PANEL_WIDTH, Math.max(MIN_PANEL_WIDTH, startWidth + delta))
    rightPanelWidth.value = newWidth
  }

  function onMouseUp() {
    isResizing = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

function toggleExpand() {
  const expandedWidth = Math.round(window.innerWidth * EXPANDED_RATIO)
  if (widthBeforeExpand !== null) {
    // restore
    rightPanelWidth.value = widthBeforeExpand
    widthBeforeExpand = null
  } else {
    widthBeforeExpand = rightPanelWidth.value
    rightPanelWidth.value = Math.min(MAX_PANEL_WIDTH, expandedWidth)
  }
}

onUnmounted(() => {
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
})

function handleGraphFilter(className) {
  fetchGraphData(className)
}

function formatSessionDate(timestamp) {
  if (!timestamp) return ''
  const d = new Date(timestamp * 1000)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  if (isToday) {
    return d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
  }
  return d.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

async function handleNewSession() {
  await createNewSession()
}

async function confirmClearNeo4j() {
  if (!confirm('Neo4j의 모든 데이터(스키마, 엔티티, 관계, 문서, 청크)가 삭제됩니다.\n정말 초기화하시겠습니까?')) {
    return
  }
  await clearNeo4jAll()
  await resetSession()
}
</script>
