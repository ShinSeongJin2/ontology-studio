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

    <div class="graph-filter" v-if="viewMode === 'entity'">
      <select
        v-if="schemas.length"
        v-model="schemaFilter"
        class="class-filter-select"
        @change="$emit('schema-filter', schemaFilter)"
      >
        <option value="">전체 스키마</option>
        <option v-for="s in schemas" :key="s.id" :value="s.name">{{ s.name }}</option>
      </select>
      <select v-model="selectedClass" class="class-filter-select" @change="$emit('filter', selectedClass)">
        <option value="">전체 클래스</option>
        <option v-for="cls in filteredClasses" :key="cls.name" :value="cls.name">{{ cls.name }}</option>
      </select>
    </div>

    <div ref="cyContainer" class="cy-container"></div>

    <div v-if="selectedNode" class="node-detail">
      <div class="node-detail-header">
        <span class="node-detail-label">{{ selectedNode.label }}</span>
        <button class="btn-icon-sm" @click="selectedNode = null">&times;</button>
      </div>
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

const emit = defineEmits(['refresh', 'filter', 'schema-filter'])

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
const schemaFilter = ref('')
const selectedNode = ref(null)
const cyContainer = ref(null)
let cy = null
// Track expanded nodes: nodeId -> Set of element IDs added by that expansion
const expandedNodes = reactive(new Map())

// Filter classes by selected schema
const filteredClasses = computed(() => {
  if (!schemaFilter.value) return props.schema.classes || []
  const schemaObj = props.schemas.find(s => s.name === schemaFilter.value)
  if (!schemaObj) return props.schema.classes || []
  const schemaClassNames = new Set((schemaObj.classes || []).map(c => c.class_name))
  return (props.schema.classes || []).filter(c => schemaClassNames.has(c.name))
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
  const classes = props.schema.classes || []

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
        labels: ['OntologyClass'],
      },
    })
  }

  for (const rel of props.schema.relationships || []) {
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
        'border-width': 3,
        'border-color': '#fff',
        'background-opacity': 1,
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
  })

  cy.on('tap', 'node', (evt) => {
    const node = evt.target
    const data = node.data()

    selectedNode.value = {
      id: data.id,
      label: data.label,
      labels: data.labels || [data.type],
      properties: data.properties || { description: data.description, count: data.count },
    }

    // Highlight neighbors
    cy.elements().removeClass('highlighted faded')
    const neighborhood = node.neighborhood().add(node)
    cy.elements().not(neighborhood).addClass('faded')
    neighborhood.addClass('highlighted')
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
      cy.elements().removeClass('highlighted faded')
    }
  })

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

watch(() => [props.graphData, props.schema, props.entityCounts], () => {
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
