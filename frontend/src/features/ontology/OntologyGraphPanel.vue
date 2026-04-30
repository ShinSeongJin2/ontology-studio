<template>
  <div class="graph-panel">
    <div class="graph-toolbar">
      <div class="view-modes">
        <button
          v-for="vm in viewModes"
          :key="vm.key"
          class="view-mode-btn"
          :class="{ active: viewMode === vm.key }"
          @click="setViewMode(vm.key)"
        >{{ vm.label }}</button>
      </div>
      <div class="graph-actions">
        <select v-model="layoutName" class="layout-select" @change="applyLayout">
          <option v-for="lo in layoutOptions" :key="lo.key" :value="lo.key">{{ lo.label }}</option>
        </select>
        <button class="btn-icon-sm" title="Fit to screen" @click="fitGraph">⊞</button>
        <button class="btn-icon-sm" title="새로고침" @click="$emit('refresh')">↻</button>
      </div>
    </div>

    <div v-if="schemas.length" class="graph-schema-bar">
      <select
        v-model="schemaFilter"
        class="class-filter-select schema-select-wide"
        @change="onSchemaFilterChange"
      >
        <option value="">전체 스키마</option>
        <option v-for="s in schemas" :key="s.id" :value="s.name">{{ s.name }}</option>
      </select>
      <div v-if="schemaFilter" class="schema-menu-wrap">
        <button
          class="btn-icon-sm btn-schema-delete"
          title="스키마 삭제"
          @click="showSchemaMenu = !showSchemaMenu"
        >🗑</button>
        <div v-if="showSchemaMenu" class="schema-delete-menu">
          <button class="schema-menu-item" @click="deleteEntitiesOnly">
            <span class="schema-menu-icon">◻</span>
            엔티티만 삭제
            <span class="schema-menu-hint">스키마 정의는 보존</span>
          </button>
          <button class="schema-menu-item schema-menu-danger" @click="deleteSchemaFull">
            <span class="schema-menu-icon">✕</span>
            스키마 전체 삭제
            <span class="schema-menu-hint">정의 + 엔티티 모두</span>
          </button>
          <button class="schema-menu-item schema-menu-cancel" @click="showSchemaMenu = false">
            취소
          </button>
        </div>
      </div>
    </div>

    <div class="graph-filter" v-if="viewMode === 'entity'">
      <select v-model="selectedClass" class="class-filter-select" @change="$emit('filter', selectedClass)">
        <option value="">전체 클래스</option>
        <option v-for="cls in filteredClasses" :key="cls.name" :value="cls.name">{{ cls.name }}</option>
      </select>
    </div>

    <div class="graph-search-bar">
      <div class="graph-search-input-wrap">
        <input
          v-model="searchQuery"
          class="graph-search-input"
          :placeholder="searchMode === 'text' ? '이름/속성으로 검색...' : '자연어로 질문 (예: 리스크 관련 프로세스는?)'"
          @keydown.enter="executeSearch"
        />
        <button v-if="searchQuery" class="graph-search-clear" @click="clearSearch">&times;</button>
      </div>
      <select v-model="searchMode" class="graph-search-mode">
        <option value="text">텍스트</option>
        <option value="nl">자연어</option>
      </select>
      <button class="graph-search-btn" :disabled="!searchQuery.trim() || isSearching" @click="executeSearch">
        {{ isSearching ? '...' : '검색' }}
      </button>
    </div>
    <div v-if="searchResultInfo" class="graph-search-info">
      <span>{{ searchResultInfo }}</span>
      <button class="graph-search-clear-btn" @click="clearSearch">초기화</button>
    </div>

    <div ref="cyContainer" class="cy-container"></div>

    <!-- Multi-selection toolbar -->
    <div v-if="multiSelectedIds.length > 1" class="multi-select-bar">
      <span class="multi-select-count">{{ multiSelectedIds.length }}개 선택</span>
      <button class="btn-multi-delete" @click="deleteMultiSelected">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14"/></svg>
        선택 삭제
      </button>
      <button class="btn-multi-cancel" @click="cy.nodes().removeClass('multi-selected'); multiSelectedIds = []">취소</button>
    </div>

    <!-- Overlay action icons at selected node -->
    <div v-if="overlayVisible && !isDragging && multiSelectedIds.length <= 1" class="node-overlay" :style="overlayStyle">
      <button class="overlay-btn overlay-delete" title="삭제" @mousedown.stop.prevent="onOverlayDelete">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14"/></svg>
      </button>
      <div class="overlay-btn overlay-create" title="드래그하여 새 노드 연결" @mousedown.stop.prevent="onDragCreateStart">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
      </div>
    </div>

    <!-- New node name input (after drop) -->
    <div v-if="newNodeInput.visible" class="new-node-input-overlay" :style="newNodeInputStyle">
      <input
        ref="newNodeNameRef"
        v-model="newNodeInput.name"
        class="node-edit-input"
        :placeholder="viewMode === 'schema' ? '클래스 이름' : '엔티티 이름'"
        @keydown.enter="confirmNewNode"
        @keydown.escape="cancelNewNode"
      />
      <div v-if="viewMode === 'entity'" style="margin-top:4px">
        <select v-model="newNodeInput.entityClass" class="node-edit-select" style="width:100%">
          <option value="" disabled>클래스 선택</option>
          <option v-for="c in filteredClasses" :key="c.name" :value="c.name">{{ c.name }}</option>
        </select>
      </div>
      <div class="rel-editor-actions">
        <button class="btn-save-node" @click="confirmNewNode">확인</button>
        <button class="btn-cancel-node" @click="cancelNewNode">취소</button>
      </div>
    </div>

    <!-- Relationship editor on new edge -->
    <div v-if="relEditor.visible" class="rel-editor-overlay" :style="relEditorStyle">
      <div class="rel-editor-title">관계 정의</div>
      <select v-model="relEditor.name" class="node-edit-select" style="width:100%">
        <option value="" disabled>기존 관계 선택...</option>
        <option v-for="r in existingRelTypes" :key="r" :value="r">{{ r }}</option>
      </select>
      <input
        v-model="relEditor.name"
        class="node-edit-input"
        placeholder="또는 새 관계명 입력"
        @keydown.enter="confirmRelation"
        @keydown.escape="cancelRelation"
      />
      <div class="rel-editor-actions">
        <button class="btn-save-node" :disabled="!relEditor.name.trim()" @click="confirmRelation">확인</button>
        <button class="btn-cancel-node" @click="cancelRelation">취소</button>
      </div>
    </div>

    <div v-if="selectedNode" class="node-detail">
      <div class="node-detail-header">
        <span class="node-detail-label">{{ selectedNode.label }}</span>
        <div class="node-detail-header-actions">
          <button v-if="(isOntologyClass || isEntity) && !editingNodeClass && !editingEntity" class="btn-icon-sm" title="편집" @click="isOntologyClass ? startEditNodeClass() : startEditEntity()">✎</button>
          <button class="btn-icon-sm" @click="selectedNode = null; editingEntity = false">&times;</button>
        </div>
      </div>

      <!-- Entity edit mode -->
      <template v-if="isEntity && editingEntity">
        <div class="node-edit-form">
          <div v-for="(val, key) in entityForm" :key="key" class="node-edit-field">
            <label class="node-edit-label">{{ key }}</label>
            <textarea
              v-if="String(val).length > 60"
              v-model="entityForm[key]"
              class="node-edit-input node-edit-textarea"
              rows="3"
            />
            <input v-else v-model="entityForm[key]" class="node-edit-input" />
          </div>
          <div class="node-edit-actions">
            <button class="btn-save-node" @click="submitEditEntity">저장</button>
            <button class="btn-cancel-node" @click="editingEntity = false">취소</button>
          </div>
        </div>
      </template>

      <!-- Ontology class edit mode -->
      <template v-else-if="isOntologyClass && editingNodeClass">
        <div class="node-edit-form">
          <label class="node-edit-label">이름</label>
          <input v-model="nodeClassForm.name" class="node-edit-input" />
          <label class="node-edit-label">설명</label>
          <input v-model="nodeClassForm.description" class="node-edit-input" />
          <label class="node-edit-label">속성</label>
          <div v-for="(prop, pi) in nodeClassForm.properties" :key="pi" class="node-edit-prop-row">
            <input v-model="prop.name" class="node-edit-input prop-input" placeholder="속성명" />
            <select v-model="prop.type" class="node-edit-select">
              <option v-for="t in PROP_TYPES" :key="t" :value="t">{{ t }}</option>
            </select>
            <button class="btn-icon-sm" @click="nodeClassForm.properties.splice(pi, 1)">✕</button>
          </div>
          <button class="node-edit-add-btn" @click="nodeClassForm.properties.push({ name: '', type: 'string' })">+ 속성 추가</button>
          <div class="node-edit-actions">
            <button class="btn-save-node" @click="submitEditNodeClass">저장</button>
            <button class="btn-cancel-node" @click="editingNodeClass = false">취소</button>
            <button class="btn-delete-node" @click="confirmDeleteNodeClass">삭제</button>
          </div>
        </div>
      </template>

      <!-- Normal view mode -->
      <template v-else>
        <div class="node-detail-badges">
          <span v-for="l in selectedNode.labels" :key="l" class="node-badge">{{ l }}</span>
        </div>
        <div class="node-detail-props">
          <div v-for="(val, key) in displayProps" :key="key" class="node-prop-row">
            <span class="node-prop-key">{{ key }}</span>
            <span class="node-prop-val">{{ truncate(formatPropValue(val), 120) }}</span>
          </div>
        </div>
        <div class="node-detail-actions">
          <button
            class="btn-traverse"
            :class="{ 'btn-collapse': expandedNodes.has(selectedNode.id) }"
            :disabled="isLoadingNeighbors"
            @click="expandedNodes.has(selectedNode.id) ? collapseNode(selectedNode.id) : expandNode(selectedNode.id)"
          >
            {{ isLoadingNeighbors ? '탐색 중...' : expandedNodes.has(selectedNode.id) ? '접기' : '이웃 노드 탐색' }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import cytoscape from 'cytoscape'

const props = defineProps({
  apiBase: { type: String, default: 'http://localhost:8000' },
  graphData: { type: Object, required: true },
  schema: { type: Object, required: true },
  schemas: { type: Array, default: () => [] },
  selectedSchema: { type: String, default: null },
  entityCounts: { type: Array, default: () => [] },
  traversedNodeIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['refresh', 'filter', 'schema-filter', 'save-class', 'delete-class', 'save-relationship', 'delete-schema-entities', 'delete-schema'])

const viewModes = [
  { key: 'schema', label: '스키마' },
  { key: 'entity', label: '엔티티' },
]

const layoutOptions = [
  { key: 'cose', label: 'Force (CoSE)' },
  { key: 'circle', label: 'Circle' },
  { key: 'concentric', label: 'Concentric' },
  { key: 'breadthfirst', label: 'Tree' },
  { key: 'grid', label: 'Grid' },
]

const viewMode = ref('schema')
const layoutName = ref('circle')
const selectedClass = ref('')
const schemaFilter = ref(props.selectedSchema || '')
const selectedNode = ref(null)
const showSchemaMenu = ref(false)

// Sync schemaFilter with parent's selectedSchema prop
watch(() => props.selectedSchema, (val) => {
  schemaFilter.value = val || ''
})
const cyContainer = ref(null)
let cy = null
// Track expanded nodes: nodeId -> Set of element IDs added by that expansion
const expandedNodes = reactive(new Map())

// Filter classes by selected schema (SQLite is source of truth — props.schema.classes comes from SQLite)
const filteredClasses = computed(() => {
  const all = props.schema.classes || []
  if (!schemaFilter.value) return all
  const schemaObj = props.schemas.find(s => s.name === schemaFilter.value)
  if (!schemaObj) return all
  const schemaClassNames = new Set((schemaObj.classes || []).map(c => c.class_name))
  return all.filter(c => schemaClassNames.has(c.name))
})

// Color palette for class nodes
const CLASS_COLORS = [
  '#018bff', '#e06c75', '#98c379', '#e5c07b', '#c678dd',
  '#56b6c2', '#be5046', '#d19a66', '#61afef', '#abb2bf',
]

function getClassColor(className, classes) {
  const idx = classes.findIndex(c => c.name === className)
  return CLASS_COLORS[idx % CLASS_COLORS.length]
}

const displayProps = computed(() => {
  if (!selectedNode.value?.properties) return {}
  const skip = new Set(['name', 'embedding'])
  const result = {}
  for (const [k, v] of Object.entries(selectedNode.value.properties)) {
    if (!skip.has(k) && v !== null && v !== undefined && v !== '') {
      result[k] = v
    }
  }
  return result
})

// ── Ontology class editing in graph panel ──
const PROP_TYPES = ['string', 'integer', 'float', 'boolean', 'date', 'list']
const editingNodeClass = ref(false)
const nodeClassForm = reactive({ name: '', description: '', properties: [] })

const isOntologyClass = computed(() => {
  return selectedNode.value?.labels?.includes('OntologyClass') ||
         selectedNode.value?.labels?.includes('_OntologyClass')
})

const isEntity = computed(() => {
  return selectedNode.value?.labels?.includes('_Entity') ||
         (selectedNode.value?.labels && !isOntologyClass.value && selectedNode.value.labels.some(l => l !== 'Relationship'))
})

// ── Entity editing ──
const editingEntity = ref(false)
const entityForm = reactive({})

function startEditEntity() {
  const p = selectedNode.value?.properties || {}
  const skip = new Set(['embedding', 'created_at', 'updated_at'])
  // Clear and repopulate
  for (const k of Object.keys(entityForm)) delete entityForm[k]
  for (const [k, v] of Object.entries(p)) {
    if (!skip.has(k) && v !== null && v !== undefined) {
      entityForm[k] = typeof v === 'object' ? JSON.stringify(v) : v
    }
  }
  editingEntity.value = true
}

async function submitEditEntity() {
  const nodeId = selectedNode.value?.id
  if (!nodeId) return
  const updatedProps = { ...entityForm }
  try {
    const apiBase = props.apiBase || 'http://localhost:8000'
    await fetch(`${apiBase}/api/graph/entities/${encodeURIComponent(nodeId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ properties: updatedProps }),
    })
    // Update local selectedNode properties
    selectedNode.value.properties = { ...selectedNode.value.properties, ...updatedProps }
    // Update cytoscape node label if name changed
    if (updatedProps.name && cy) {
      const cyNode = cy.getElementById(nodeId)
      if (cyNode.length) cyNode.data('label', updatedProps.name)
    }
    editingEntity.value = false
  } catch (err) {
    console.error('Entity update failed:', err)
  }
}

function startEditNodeClass() {
  const node = selectedNode.value
  if (!node) return
  const p = node.properties || {}
  nodeClassForm.name = p.name || node.label || ''
  nodeClassForm.description = p.description || ''
  // Use the original schema properties array stored during graph build
  const schemaProps = node.schemaProperties
  if (Array.isArray(schemaProps) && schemaProps.length > 0) {
    nodeClassForm.properties = schemaProps.map(pr => ({ name: pr.name || '', type: pr.type || 'string' }))
  } else {
    // Fallback: extract from display properties (keys like "[속성] content")
    const extracted = []
    for (const [k, v] of Object.entries(p)) {
      if (k.startsWith('[속성] ')) {
        extracted.push({ name: k.replace('[속성] ', ''), type: String(v).toLowerCase() || 'string' })
      }
    }
    nodeClassForm.properties = extracted
  }
  editingNodeClass.value = true
}

function submitEditNodeClass() {
  if (!nodeClassForm.name.trim()) return
  const validProps = nodeClassForm.properties.filter(p => p.name.trim())
  // Find which schema this class belongs to
  const originalName = selectedNode.value?.properties?.name || selectedNode.value?.label || ''
  let schemaName = ''
  for (const s of props.schemas) {
    if ((s.classes || []).some(c => c.class_name === originalName)) {
      schemaName = s.name
      break
    }
  }
  emit('save-class', {
    name: nodeClassForm.name.trim(),
    original_name: originalName,
    description: nodeClassForm.description,
    properties: validProps,
    schema_name: schemaName,
  })
  editingNodeClass.value = false
}

function getEntityCount(className) {
  return props.entityCounts.find(e => e.name === className)?.count || 0
}

function confirmDeleteNodeClass() {
  const name = selectedNode.value?.properties?.name || selectedNode.value?.label
  if (!name) return
  const count = getEntityCount(name)
  const msg = count > 0
    ? `클래스 "${name}"을(를) 삭제하시겠습니까?\n\n⚠ 이 클래스의 엔티티 ${count}개도 함께 삭제됩니다.`
    : `클래스 "${name}"을(를) 삭제하시겠습니까?`
  if (confirm(msg)) {
    emit('delete-class', name)
    editingNodeClass.value = false
    selectedNode.value = null
  }
}

function onSchemaFilterChange() {
  emit('schema-filter', schemaFilter.value)
}

function deleteEntitiesOnly() {
  const s = props.schemas.find(sc => sc.name === schemaFilter.value)
  if (!s) return
  showSchemaMenu.value = false
  emit('delete-schema-entities', s.id)
}

function deleteSchemaFull() {
  const s = props.schemas.find(sc => sc.name === schemaFilter.value)
  if (!s) return
  showSchemaMenu.value = false
  emit('delete-schema', s.id)
  schemaFilter.value = ''
}

// ── Search / NL Query ──
const searchQuery = ref('')
const searchMode = ref('text')
const isSearching = ref(false)
const searchResultInfo = ref('')

async function executeSearch() {
  if (!searchQuery.value.trim()) return
  isSearching.value = true
  searchResultInfo.value = ''

  try {
    let result
    if (searchMode.value === 'text') {
      const params = new URLSearchParams({ q: searchQuery.value.trim(), limit: '50' })
      if (selectedClass.value) params.set('class_name', selectedClass.value)
      const res = await fetch(`${props.apiBase}/api/graph/search?${params}`)
      result = await res.json()
    } else {
      const res = await fetch(`${props.apiBase}/api/graph/nl-query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: searchQuery.value.trim() }),
      })
      result = await res.json()
      if (result.cypher) {
        searchResultInfo.value = `Cypher: ${result.cypher.substring(0, 80)}${result.cypher.length > 80 ? '...' : ''}`
      }
    }

    if (result.error) {
      searchResultInfo.value = `오류: ${result.error}`
      return
    }

    const matchedNodes = result.nodes || []
    if (matchedNodes.length === 0) {
      searchResultInfo.value = `${searchResultInfo.value ? searchResultInfo.value + ' — ' : ''}결과 없음`
      return
    }

    // Highlight matched nodes in graph
    if (cy) {
      const matchedIds = new Set(matchedNodes.map(n => n.id))
      cy.elements().removeClass('highlighted faded search-match')

      // For entity view: check if matched nodes exist in current graph
      const existingMatched = cy.nodes().filter(n => matchedIds.has(n.data('id')))
      if (existingMatched.length > 0) {
        existingMatched.addClass('search-match')
        cy.elements().not(existingMatched).addClass('faded')
        cy.animate({ fit: { eles: existingMatched, padding: 50 }, duration: 400 })
        searchResultInfo.value = `${searchResultInfo.value ? searchResultInfo.value + ' — ' : ''}${existingMatched.length}개 노드 하이라이트`
      } else {
        // Nodes not in current view — add them temporarily
        const classes = props.schema.classes || []
        for (const node of matchedNodes) {
          if (cy.getElementById(node.id).length === 0) {
            const primaryLabel = (node.labels || []).find(l => l !== '_Entity') || 'Unknown'
            cy.add({
              group: 'nodes',
              data: {
                id: node.id,
                label: node.label || node.id,
                type: 'entity',
                entityClass: primaryLabel,
                color: getClassColor(primaryLabel, classes),
                properties: node.properties,
                labels: node.labels,
              },
              classes: 'search-match',
            })
          }
        }
        const added = cy.nodes('.search-match')
        cy.elements().not(added).addClass('faded')
        cy.layout(getLayoutConfig(layoutName.value)).run()
        searchResultInfo.value = `${searchResultInfo.value ? searchResultInfo.value + ' — ' : ''}${matchedNodes.length}개 노드 추가됨`
      }
    }
  } catch (err) {
    searchResultInfo.value = `오류: ${err.message}`
  } finally {
    isSearching.value = false
  }
}

function clearSearch() {
  searchQuery.value = ''
  searchResultInfo.value = ''
  if (cy) {
    cy.elements().removeClass('search-match faded highlighted')
  }
}

// ── Multi-selection ──
const multiSelectedIds = ref([])

function updateMultiSelection() {
  if (!cy) return
  const selected = cy.nodes('.multi-selected')
  // Also include cytoscape :selected (from box selection)
  const boxSelected = cy.nodes(':selected')
  boxSelected.addClass('multi-selected')
  multiSelectedIds.value = cy.nodes('.multi-selected').map(n => n.data('id'))
  // Hide single-node overlay when multi-selecting
  if (multiSelectedIds.value.length > 1) {
    hideOverlay()
    selectedNode.value = null
  }
}

async function deleteMultiSelected() {
  const ids = [...multiSelectedIds.value]
  if (!ids.length) return

  const count = ids.length
  if (!confirm(`선택된 ${count}개 노드를 삭제하시겠습니까?`)) return

  for (const id of ids) {
    const node = cy.getElementById(id)
    if (!node.length) continue
    const data = node.data()

    if (data.type === 'class') {
      emit('delete-class', data.label)
    } else if (data.type === 'entity') {
      await fetch(`${props.apiBase}/api/graph/entities/${encodeURIComponent(id)}`, { method: 'DELETE' })
      node.remove()
    }
  }

  cy.nodes().removeClass('multi-selected')
  multiSelectedIds.value = []
  selectedNode.value = null
}

// ── Overlay actions ──
const overlayVisible = ref(false)
const overlayPos = reactive({ x: 0, y: 0 })
const overlayNodeId = ref(null)

const overlayStyle = computed(() => ({
  left: `${overlayPos.x}px`,
  top: `${overlayPos.y}px`,
}))

function showOverlay(nodeId) {
  if (!cy) return
  const node = cy.getElementById(nodeId)
  if (!node.length) return
  const rp = node.renderedPosition()
  overlayPos.x = rp.x
  overlayPos.y = rp.y
  overlayNodeId.value = nodeId
  overlayVisible.value = true
}

function hideOverlay() {
  overlayVisible.value = false
  overlayNodeId.value = null
}

function updateOverlayPosition() {
  if (!overlayVisible.value || !overlayNodeId.value || !cy) return
  const node = cy.getElementById(overlayNodeId.value)
  if (node.length) {
    const rp = node.renderedPosition()
    overlayPos.x = rp.x
    overlayPos.y = rp.y
  }
}

// ── Delete via overlay ──
async function onOverlayDelete() {
  const nodeId = overlayNodeId.value
  if (!nodeId) return
  const node = cy?.getElementById(nodeId)
  if (!node?.length) return

  const data = node.data()
  if (data.type === 'class') {
    // Schema class
    const count = getEntityCount(data.label)
    const msg = count > 0
      ? `클래스 "${data.label}"을(를) 삭제하시겠습니까?\n\n⚠ 이 클래스의 엔티티 ${count}개도 함께 삭제됩니다.`
      : `클래스 "${data.label}"을(를) 삭제하시겠습니까?`
    if (confirm(msg)) {
      emit('delete-class', data.label)
      hideOverlay()
      selectedNode.value = null
    }
  } else if (data.type === 'entity') {
    // Entity instance
    if (confirm(`엔티티 "${data.label}"을(를) 삭제하시겠습니까?`)) {
      await fetch(`${props.apiBase}/api/graph/entities/${encodeURIComponent(nodeId)}`, { method: 'DELETE' })
      node.remove()
      hideOverlay()
      selectedNode.value = null
    }
  }
}

// ── Drag to create ──
const isDragging = ref(false)
const dragSourceId = ref(null)
const newNodeNameRef = ref(null)

const newNodeInput = reactive({
  visible: false,
  name: '',
  entityClass: '',
  pos: { x: 0, y: 0 },  // rendered coords
  modelPos: { x: 0, y: 0 },  // cytoscape model coords
})
const newNodeInputStyle = computed(() => ({
  left: `${newNodeInput.pos.x}px`,
  top: `${newNodeInput.pos.y + 20}px`,
}))

const relEditor = reactive({
  visible: false,
  name: '',
  sourceId: '',
  targetId: '',
  pos: { x: 0, y: 0 },
})
const relEditorStyle = computed(() => ({
  left: `${relEditor.pos.x}px`,
  top: `${relEditor.pos.y}px`,
}))

const existingRelTypes = computed(() => {
  const names = new Set()
  for (const r of props.schema.relationships || []) names.add(r.name)
  return [...names]
})

// Pending creation for rollback
const pendingCreation = reactive({
  active: false,
  sourceId: '',
  newNodeId: '',
  newNodeLabel: '',
  isSchema: false,
})

function onDragCreateStart(e) {
  if (!cy || !overlayNodeId.value) return
  isDragging.value = true
  dragSourceId.value = overlayNodeId.value
  hideOverlay()

  const rect = cyContainer.value.getBoundingClientRect()

  // Create ghost node + edge
  const startModelPos = cy.getElementById(dragSourceId.value).position()
  cy.add([
    {
      group: 'nodes',
      data: { id: '__ghost_target', label: '?', type: viewMode.value === 'schema' ? 'class' : 'entity', color: '#888' },
      position: { ...startModelPos },
      classes: 'ghost',
    },
    {
      group: 'edges',
      data: { id: '__ghost_edge', source: dragSourceId.value, target: '__ghost_target', label: '' },
      classes: 'ghost',
    },
  ])

  cy.panningEnabled(false)
  cy.userPanningEnabled(false)

  function onMouseMove(ev) {
    const rx = ev.clientX - rect.left
    const ry = ev.clientY - rect.top
    const mx = (rx - cy.pan().x) / cy.zoom()
    const my = (ry - cy.pan().y) / cy.zoom()
    cy.getElementById('__ghost_target').position({ x: mx, y: my })
  }

  function onMouseUp(ev) {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    cy.panningEnabled(true)
    cy.userPanningEnabled(true)
    isDragging.value = false

    const rx = ev.clientX - rect.left
    const ry = ev.clientY - rect.top
    const mx = (rx - cy.pan().x) / cy.zoom()
    const my = (ry - cy.pan().y) / cy.zoom()

    // Check if dropped on an existing node
    const dropTarget = cy.nodes().filter(n => {
      if (n.data('id') === '__ghost_target' || n.data('id') === dragSourceId.value) return false
      const pos = n.renderedPosition()
      return Math.abs(pos.x - rx) < 25 && Math.abs(pos.y - ry) < 25
    })

    // Remove ghost elements
    cy.getElementById('__ghost_edge').remove()
    cy.getElementById('__ghost_target').remove()

    if (dropTarget.length > 0) {
      // Dropped on existing node → go straight to relationship editor
      const targetData = dropTarget[0].data()
      showRelEditor(dragSourceId.value, targetData.id)
    } else {
      // Dropped on empty space → show name input
      newNodeInput.name = ''
      newNodeInput.entityClass = ''
      newNodeInput.pos = { x: rx, y: ry }
      newNodeInput.modelPos = { x: mx, y: my }
      newNodeInput.visible = true
      nextTick(() => newNodeNameRef.value?.focus())
    }
  }

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

async function confirmNewNode() {
  if (!newNodeInput.name.trim()) return
  if (viewMode.value === 'entity' && !newNodeInput.entityClass) return

  const name = newNodeInput.name.trim()
  newNodeInput.visible = false

  if (viewMode.value === 'schema') {
    // Find schema name
    let schemaName = ''
    for (const s of props.schemas) {
      const srcData = cy.getElementById(dragSourceId.value)?.data()
      if ((s.classes || []).some(c => c.class_name === srcData?.label)) {
        schemaName = s.name
        break
      }
    }

    // Create temporary local node
    const newId = `class:${name}`
    const classes = filteredClasses.value
    cy.add({
      group: 'nodes',
      data: {
        id: newId, label: name, type: 'class',
        color: CLASS_COLORS[classes.length % CLASS_COLORS.length],
        properties: { description: '' }, labels: ['OntologyClass'],
        schemaProperties: [],
      },
      position: newNodeInput.modelPos,
    })

    pendingCreation.active = true
    pendingCreation.sourceId = dragSourceId.value
    pendingCreation.newNodeId = newId
    pendingCreation.newNodeLabel = name
    pendingCreation.isSchema = true
    pendingCreation.schemaName = schemaName

    showRelEditor(dragSourceId.value, newId)
  } else {
    // Entity view — create via API
    const entityClass = newNodeInput.entityClass
    try {
      const res = await fetch(`${props.apiBase}/api/graph/entities`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ class_name: entityClass, properties: { name }, match_keys: ['name'] }),
      })
      const result = await res.json()
      if (result.error) { alert(result.error); return }

      const newId = result.entity?.element_id || `entity:${name}`
      const classes = props.schema.classes || []
      cy.add({
        group: 'nodes',
        data: {
          id: newId, label: name, type: 'entity',
          entityClass, color: getClassColor(entityClass, classes),
          properties: result.entity?.properties || { name },
          labels: ['_Entity', entityClass],
        },
        position: newNodeInput.modelPos,
      })

      pendingCreation.active = true
      pendingCreation.sourceId = dragSourceId.value
      pendingCreation.newNodeId = newId
      pendingCreation.newNodeLabel = name
      pendingCreation.isSchema = false

      showRelEditor(dragSourceId.value, newId)
    } catch (err) {
      console.error('Entity creation failed:', err)
    }
  }
}

function cancelNewNode() {
  newNodeInput.visible = false
  // Remove ghost if any
  cy?.getElementById('__ghost_target')?.remove()
  cy?.getElementById('__ghost_edge')?.remove()
}

function showRelEditor(sourceId, targetId) {
  if (!cy) return
  const srcPos = cy.getElementById(sourceId).renderedPosition()
  const tgtPos = cy.getElementById(targetId).renderedPosition()
  relEditor.name = ''
  relEditor.sourceId = sourceId
  relEditor.targetId = targetId
  relEditor.pos = {
    x: (srcPos.x + tgtPos.x) / 2,
    y: (srcPos.y + tgtPos.y) / 2,
  }
  relEditor.visible = true
}

async function confirmRelation() {
  if (!relEditor.name.trim()) return
  const relName = relEditor.name.trim()
  relEditor.visible = false

  if (pendingCreation.isSchema) {
    // First save the class, then the relationship
    const srcLabel = cy.getElementById(pendingCreation.sourceId)?.data('label')
    const tgtLabel = pendingCreation.newNodeLabel

    emit('save-class', {
      name: tgtLabel,
      description: '',
      properties: [],
      schema_name: pendingCreation.schemaName || '',
    })
    emit('save-relationship', {
      name: relName,
      from_class: srcLabel,
      to_class: tgtLabel,
      description: '',
      properties: [],
      schema_name: pendingCreation.schemaName || '',
    })
  } else {
    // Entity relationship via API
    await fetch(`${props.apiBase}/api/graph/relationships`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_id: relEditor.sourceId,
        to_id: relEditor.targetId,
        rel_type: relName,
        properties: {},
      }),
    })
    // Add edge visually
    cy.add({
      group: 'edges',
      data: {
        id: `edge:${relEditor.sourceId}:${relEditor.targetId}:${relName}`,
        source: relEditor.sourceId,
        target: relEditor.targetId,
        label: relName,
        type: 'entity-rel',
      },
    })
  }

  resetPending()
}

async function cancelRelation() {
  relEditor.visible = false

  // Rollback: remove newly created node
  if (pendingCreation.active && pendingCreation.newNodeId) {
    if (pendingCreation.isSchema) {
      // Just remove from local graph (not yet persisted)
      cy?.getElementById(pendingCreation.newNodeId)?.remove()
    } else {
      // Delete entity from backend
      await fetch(`${props.apiBase}/api/graph/entities/${encodeURIComponent(pendingCreation.newNodeId)}`, { method: 'DELETE' })
      cy?.getElementById(pendingCreation.newNodeId)?.remove()
    }
  }

  resetPending()
}

function resetPending() {
  pendingCreation.active = false
  pendingCreation.sourceId = ''
  pendingCreation.newNodeId = ''
  pendingCreation.newNodeLabel = ''
  pendingCreation.isSchema = false
}

function truncate(str, max) {
  return str.length > max ? str.slice(0, max) + '…' : str
}

function padNumber(value) {
  return String(value).padStart(2, '0')
}

function formatNeo4jTemporalObject(value) {
  if (!value || typeof value !== 'object') return null

  const datePart = value._DateTime__date || value
  const timePart = value._DateTime__time
  const year = datePart._Date__year
  const month = datePart._Date__month
  const day = datePart._Date__day

  if (!year || !month || !day) return null

  if (!timePart) {
    return `${year}-${padNumber(month)}-${padNumber(day)}`
  }

  const hour = timePart._Time__hour ?? 0
  const minute = timePart._Time__minute ?? 0
  const second = timePart._Time__second ?? 0
  return `${year}-${padNumber(month)}-${padNumber(day)} ${padNumber(hour)}:${padNumber(minute)}:${padNumber(second)}`
}

function formatPropValue(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.every(item => item === null || ['string', 'number', 'boolean'].includes(typeof item))
      ? value.join(', ')
      : JSON.stringify(value)
  }

  const temporalValue = formatNeo4jTemporalObject(value)
  if (temporalValue) return temporalValue

  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function buildSchemaElements() {
  const nodes = []
  const edges = []
  const classes = filteredClasses.value
  const classNameSet = new Set(classes.map(c => c.name))

  for (const cls of classes) {
    const count = props.entityCounts.find(e => e.name === cls.name)?.count || 0
    // Build properties object for detail panel
    const classProps = { description: cls.description || '' }
    if (count > 0) classProps['노드 수'] = count
    if (cls.properties?.length) {
      classProps['속성'] = cls.properties.map(p => `${p.name}: ${p.type || 'STRING'}`).join(', ')
      for (const p of cls.properties) {
        classProps[`[속성] ${p.name}`] = p.type || 'STRING'
      }
    }
    nodes.push({
      data: {
        id: `class:${cls.name}`,
        label: cls.name,
        description: cls.description || '',
        count,
        type: 'class',
        color: getClassColor(cls.name, classes),
        properties: classProps,
        // Keep original schema properties array for editing
        schemaProperties: cls.properties || [],
        labels: ['OntologyClass'],
      },
    })
  }

  // Filter relationships to only show those between filtered classes
  const rels = (props.schema.relationships || []).filter(
    r => classNameSet.has(r.from_class) && classNameSet.has(r.to_class)
  )
  for (const rel of rels) {
    const relProps = {
      description: rel.description || '',
      'from': rel.from_class,
      'to': rel.to_class,
    }
    if (rel.properties?.length) {
      for (const p of rel.properties) {
        relProps[`[속성] ${p.name}`] = p.type || 'STRING'
      }
    }
    edges.push({
      data: {
        id: `rel:${rel.name}:${rel.from_class}:${rel.to_class}`,
        source: `class:${rel.from_class}`,
        target: `class:${rel.to_class}`,
        label: rel.name,
        type: 'schema-rel',
        properties: relProps,
        labels: ['Relationship'],
      },
    })
  }

  return [...nodes, ...edges]
}

function buildEntityElements() {
  const nodes = []
  const edges = []
  const classes = props.schema.classes || []

  for (const node of props.graphData.nodes || []) {
    const primaryLabel = (node.labels || []).find(l => l !== '_Entity') || 'Unknown'
    nodes.push({
      data: {
        id: node.id,
        label: node.label || node.properties?.name || node.id,
        type: 'entity',
        entityClass: primaryLabel,
        color: getClassColor(primaryLabel, classes),
        properties: node.properties,
        labels: node.labels,
      },
    })
  }

  for (const edge of props.graphData.edges || []) {
    edges.push({
      data: {
        id: `edge:${edge.from}:${edge.to}:${edge.type}`,
        source: edge.from,
        target: edge.to,
        label: edge.type,
        type: 'entity-rel',
      },
    })
  }

  return [...nodes, ...edges]
}

function getLayoutConfig(name) {
  const base = { animate: true, animationDuration: 300, padding: 30 }
  switch (name) {
    case 'cose':
      return { ...base, name: 'cose', nodeRepulsion: 8000, idealEdgeLength: 120, randomize: true }
    case 'circle':
      return { ...base, name: 'circle', padding: 40 }
    case 'concentric':
      return { ...base, name: 'concentric', concentric: (n) => n.degree(), levelWidth: () => 2 }
    case 'breadthfirst':
      return { ...base, name: 'breadthfirst', directed: true, spacingFactor: 1.2 }
    case 'grid':
      return { ...base, name: 'grid', rows: undefined }
    default:
      return { ...base, name: 'circle' }
  }
}

function getLayout() {
  return getLayoutConfig(layoutName.value)
}

function applyLayout() {
  if (!cy) return
  cy.layout(getLayoutConfig(layoutName.value)).run()
}

function getCytoscapeStyle() {
  return [
    {
      selector: 'node[type="class"]',
      style: {
        'background-color': 'data(color)',
        'label': 'data(label)',
        'color': '#e0e0e0',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 8,
        'font-size': '11px',
        'font-weight': 'bold',
        'width': 50,
        'height': 50,
        'border-width': 2,
        'border-color': 'data(color)',
        'border-opacity': 0.6,
        'text-outline-color': '#1e1e2e',
        'text-outline-width': 2,
      },
    },
    {
      selector: 'node[type="entity"]',
      style: {
        'background-color': 'data(color)',
        'label': 'data(label)',
        'color': '#c0c0c0',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-y': 6,
        'font-size': '9px',
        'width': 30,
        'height': 30,
        'text-outline-color': '#1e1e2e',
        'text-outline-width': 1.5,
        'text-max-width': '80px',
        'text-wrap': 'ellipsis',
      },
    },
    {
      selector: 'edge',
      style: {
        'width': 1.5,
        'line-color': '#555',
        'target-arrow-color': '#555',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'label': 'data(label)',
        'font-size': '8px',
        'color': '#888',
        'text-rotation': 'autorotate',
        'text-outline-color': '#1e1e2e',
        'text-outline-width': 1,
      },
    },
    {
      selector: 'edge[type="schema-rel"]',
      style: {
        'width': 2,
        'line-color': '#666',
        'target-arrow-color': '#666',
        'font-size': '9px',
      },
    },
    {
      selector: 'node:selected',
      style: {
        'border-width': 4,
        'border-color': '#ffcc00',
        'border-style': 'double',
        'background-opacity': 1,
      },
    },
    {
      selector: 'node.multi-selected',
      style: {
        'border-width': 4,
        'border-color': '#ffcc00',
        'border-style': 'double',
        'background-opacity': 1,
        'z-index': 10,
      },
    },
    {
      selector: 'node.highlighted',
      style: {
        'border-width': 3,
        'border-color': '#ffcc00',
        'background-opacity': 1,
      },
    },
    {
      selector: 'node.faded',
      style: {
        'opacity': 0.2,
      },
    },
    {
      selector: 'edge.faded',
      style: {
        'opacity': 0.1,
      },
    },
    {
      selector: 'node.traversed',
      style: {
        'border-width': 4,
        'border-color': '#ff6a00',
        'border-style': 'double',
        'background-opacity': 1,
        'z-index': 10,
      },
    },
    {
      selector: 'edge.traversed-path',
      style: {
        'line-color': '#ff6a00',
        'target-arrow-color': '#ff6a00',
        'width': 2.5,
        'opacity': 1,
        'z-index': 10,
      },
    },
    {
      selector: 'node.search-match',
      style: {
        'border-width': 3,
        'border-color': '#00e676',
        'background-opacity': 1,
        'z-index': 10,
      },
    },
    {
      selector: 'node.ghost',
      style: {
        'background-color': '#888',
        'opacity': 0.4,
        'border-style': 'dashed',
        'border-width': 2,
        'border-color': '#aaa',
        'label': '?',
        'width': 35,
        'height': 35,
      },
    },
    {
      selector: 'edge.ghost',
      style: {
        'line-style': 'dashed',
        'line-color': '#aaa',
        'target-arrow-color': '#aaa',
        'opacity': 0.5,
      },
    },
  ]
}

function initCytoscape() {
  if (!cyContainer.value) return

  if (cy) {
    cy.destroy()
    cy = null
  }

  const elements = viewMode.value === 'schema'
    ? buildSchemaElements()
    : buildEntityElements()

  if (elements.length === 0) return

  cy = cytoscape({
    container: cyContainer.value,
    elements,
    style: getCytoscapeStyle(),
    layout: getLayout(),
    minZoom: 0.3,
    maxZoom: 3,
    wheelSensitivity: 0.3,
    boxSelectionEnabled: true,
    selectionType: 'additive',
  })

  // Expose for E2E testing
  if (cyContainer.value) {
    cyContainer.value.__cy = cy
  }

  cy.on('tap', 'node', (evt) => {
    const node = evt.target
    const data = node.data()
    if (data.id === '__ghost_target') return
    const isShift = evt.originalEvent?.shiftKey

    if (isShift) {
      // Shift+click: toggle multi-selection
      node.toggleClass('multi-selected')
      updateMultiSelection()
      return
    }

    // Clear previous multi-selection on normal click
    cy.nodes().removeClass('multi-selected')
    multiSelectedIds.value = []

    selectedNode.value = {
      id: data.id,
      label: data.label,
      labels: data.labels || [data.type],
      properties: data.properties || { description: data.description, count: data.count },
      schemaProperties: data.schemaProperties || [],
    }

    // Show overlay actions
    showOverlay(data.id)
    editingNodeClass.value = false

    // Highlight neighbors
    cy.elements().removeClass('highlighted faded')
    const neighborhood = node.neighborhood().add(node)
    cy.elements().not(neighborhood).addClass('faded')
    neighborhood.addClass('highlighted')
  })

  // Box selection (lasso) complete
  cy.on('boxend', () => {
    updateMultiSelection()
  })

  cy.on('tap', 'edge', (evt) => {
    const edge = evt.target
    const data = edge.data()

    if (data.properties) {
      selectedNode.value = {
        id: data.id,
        label: data.label,
        labels: data.labels || ['Relationship'],
        properties: data.properties,
      }
    }
  })

  cy.on('tap', (evt) => {
    if (evt.target === cy) {
      selectedNode.value = null
      hideOverlay()
      cy.elements().removeClass('highlighted faded multi-selected')
      multiSelectedIds.value = []
    }
  })

  cy.on('viewport', () => updateOverlayPosition())

  cy.on('dbltap', 'node[type="entity"]', (evt) => {
    const nodeId = evt.target.data('id')
    if (expandedNodes.has(nodeId)) {
      collapseNode(nodeId)
    } else {
      expandNode(nodeId)
    }
  })

  // Apply traversal highlighting if there are active traversed nodes
  if (props.traversedNodeIds.length > 0) {
    cy.one('layoutstop', () => {
      highlightTraversedNodes(props.traversedNodeIds)
    })
  }
}

function fitGraph() {
  if (cy) {
    cy.fit(undefined, 30)
    cy.center()
  }
}

function setViewMode(mode) {
  viewMode.value = mode
  layoutName.value = mode === 'schema' ? 'circle' : 'cose'
  selectedNode.value = null
  expandedNodes.clear()
  nextTick(() => initCytoscape())
}

const isLoadingNeighbors = ref(false)

async function expandNode(nodeId) {
  if (!cy || isLoadingNeighbors.value) return
  isLoadingNeighbors.value = true

  try {
    const res = await fetch(`${props.apiBase}/api/graph/neighbors?node_id=${encodeURIComponent(nodeId)}`)
    const data = await res.json()
    const classes = props.schema.classes || []
    const addedIds = new Set()

    // Add new nodes that don't already exist in the graph
    for (const node of data.nodes || []) {
      if (cy.getElementById(node.id).length === 0) {
        const primaryLabel = (node.labels || []).find(l => l !== '_Entity') || 'Unknown'
        cy.add({
          group: 'nodes',
          data: {
            id: node.id,
            label: node.label || node.properties?.name || node.id,
            type: 'entity',
            entityClass: primaryLabel,
            color: getClassColor(primaryLabel, classes),
            properties: node.properties,
            labels: node.labels,
          },
        })
        addedIds.add(node.id)
      }
    }

    // Add new edges
    for (const edge of data.edges || []) {
      const edgeId = `edge:${edge.from}:${edge.to}:${edge.type}`
      if (cy.getElementById(edgeId).length === 0
          && cy.getElementById(edge.from).length > 0
          && cy.getElementById(edge.to).length > 0) {
        cy.add({
          group: 'edges',
          data: {
            id: edgeId,
            source: edge.from,
            target: edge.to,
            label: edge.type,
            type: 'entity-rel',
          },
        })
        addedIds.add(edgeId)
      }
    }

    // Track what was added so we can collapse later
    expandedNodes.set(nodeId, addedIds)

    // Re-run layout
    cy.layout(getLayoutConfig(layoutName.value)).run()

    // Highlight the expanded node and its neighbors
    nextTick(() => {
      const sourceNode = cy.getElementById(nodeId)
      if (sourceNode.length) {
        cy.elements().removeClass('highlighted faded')
        const neighborhood = sourceNode.neighborhood().add(sourceNode)
        cy.elements().not(neighborhood).addClass('faded')
        neighborhood.addClass('highlighted')
      }
    })
  } catch {
    // ignore
  } finally {
    isLoadingNeighbors.value = false
  }
}

function collapseNode(nodeId) {
  if (!cy) return
  const addedIds = expandedNodes.get(nodeId)
  if (!addedIds) return

  // Collect IDs that are exclusively owned by this expansion
  // (not also added by another expansion)
  const otherAdded = new Set()
  for (const [otherId, otherSet] of expandedNodes) {
    if (otherId !== nodeId) {
      for (const id of otherSet) otherAdded.add(id)
    }
  }

  for (const id of addedIds) {
    if (otherAdded.has(id)) continue
    const ele = cy.getElementById(id)
    if (ele.length) {
      // Also remove connected edges of removed nodes
      if (ele.isNode()) {
        ele.connectedEdges().remove()
      }
      ele.remove()
    }
  }

  expandedNodes.delete(nodeId)
  selectedNode.value = null
  cy.elements().removeClass('highlighted faded')

  // Re-run layout
  cy.layout(getLayoutConfig(layoutName.value)).run()
}

watch(() => [props.graphData, props.schema, props.entityCounts, schemaFilter.value], () => {
  nextTick(() => initCytoscape())
}, { deep: true })

watch(() => props.traversedNodeIds, (ids) => {
  if (!ids.length) {
    // Clear traversal highlighting
    if (cy) cy.elements().removeClass('traversed traversed-path faded')
    return
  }
  // Auto-switch to entity view if in schema view
  if (viewMode.value === 'schema') {
    viewMode.value = 'entity'
    layoutName.value = 'cose'
    nextTick(() => initCytoscape()) // initCytoscape will apply highlighting via layoutstop
    return
  }
  if (cy) highlightTraversedNodes(ids)
}, { deep: true })

function highlightTraversedNodes(ids) {
  if (!cy || !ids.length) return

  cy.elements().removeClass('traversed traversed-path faded')

  const traversedSet = new Set(ids)
  const matched = cy.nodes().filter(n => traversedSet.has(n.data('id')))

  if (matched.length === 0) return

  matched.addClass('traversed')

  // Highlight edges between traversed nodes
  matched.edgesWith(matched).addClass('traversed-path')

  // Fade non-traversed elements
  const related = matched.union(matched.edgesWith(matched))
  cy.elements().not(related).addClass('faded')

  // Fit view to traversed nodes
  cy.animate({
    fit: { eles: matched, padding: 50 },
    duration: 400,
  })
}

onMounted(() => {
  nextTick(() => initCytoscape())
})

onUnmounted(() => {
  if (cy) {
    cy.destroy()
    cy = null
  }
})
</script>
