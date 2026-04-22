<template>
  <CollapsiblePanel title="온톨로지 스키마" :open="open" @toggle="$emit('toggle')">
    <div v-if="!schema.classes.length && !schema.relationships.length && !schemas.length" class="empty-hint">
      스키마가 아직 없습니다. 대화로 설계를 시작하세요.
    </div>

    <!-- Schema Groups -->
    <div v-for="s in schemas" :key="s.id" class="schema-group">
      <div
        class="schema-group-header"
        :class="{ active: selectedSchema === s.name }"
        @click="$emit('select-schema', s.name === selectedSchema ? null : s.name)"
      >
        <span class="schema-group-toggle">{{ expandedGroups[s.id] ? '▼' : '▶' }}</span>
        <span class="schema-group-name" @click.stop="toggleGroup(s.id)">{{ s.name }}</span>
        <span class="schema-group-count">{{ s.total_entity_count || 0 }}</span>
        <div class="schema-group-actions">
          <button
            class="btn-icon-xs"
            title="엔티티 삭제"
            @click.stop="$emit('delete-schema-entities', s.id)"
          >🗑</button>
          <button
            class="btn-icon-xs"
            title="스키마 복원 (Neo4j)"
            @click.stop="$emit('rebuild-schema', s.id)"
          >↻</button>
        </div>
      </div>
      <div v-if="expandedGroups[s.id]" class="schema-group-body">
        <div v-for="cls in s.classes" :key="cls.class_name" class="schema-class indented">
          <div class="schema-class-header">
            <span class="schema-icon">C</span>
            <span class="schema-class-name">{{ cls.class_name }}</span>
            <span v-if="cls.entity_count" class="entity-count-badge">{{ cls.entity_count }}</span>
          </div>
          <div v-if="cls.description" class="schema-class-desc">{{ cls.description }}</div>
          <div v-for="prop in cls.properties || []" :key="prop.name" class="schema-prop">
            <span class="prop-name">{{ prop.name }}</span>
            <span class="prop-type">{{ prop.type }}</span>
            <span v-if="prop.required" class="prop-required">*</span>
          </div>
        </div>
        <div v-if="s.relationships && s.relationships.length" class="schema-rels-section indented">
          <div v-for="rel in s.relationships" :key="`${rel.name}-${rel.from_class}-${rel.to_class}`" class="schema-rel">
            <span class="rel-from">{{ rel.from_class }}</span>
            <span class="rel-arrow">-[{{ rel.name }}]-></span>
            <span class="rel-to">{{ rel.to_class }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Ungrouped classes (in Neo4j but not in any SQLite schema) -->
    <div v-if="ungroupedClasses.length" class="schema-group">
      <div class="schema-group-header ungrouped" @click="showUngrouped = !showUngrouped">
        <span class="schema-group-toggle">{{ showUngrouped ? '▼' : '▶' }}</span>
        <span class="schema-group-name">(미분류)</span>
        <span class="schema-group-count">{{ ungroupedClasses.length }}</span>
      </div>
      <div v-if="showUngrouped" class="schema-group-body">
        <div v-for="cls in ungroupedClasses" :key="cls.name" class="schema-class indented">
          <div class="schema-class-header">
            <span class="schema-icon">C</span>
            <span class="schema-class-name">{{ cls.name }}</span>
          </div>
          <div v-if="cls.description" class="schema-class-desc">{{ cls.description }}</div>
          <div v-for="prop in cls.properties || []" :key="prop.name" class="schema-prop">
            <span class="prop-name">{{ prop.name }}</span>
            <span class="prop-type">{{ prop.type }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Entity counts (shown when no schema groups exist) -->
    <div v-if="!schemas.length && entityCounts.length" class="schema-rels-section">
      <div class="ctx-label">엔티티 수</div>
      <div v-for="entityCount in entityCounts" :key="entityCount.name" class="entity-count-item">
        <span>{{ entityCount.name }}</span>
        <span class="entity-count-badge">{{ entityCount.count }}</span>
      </div>
    </div>
  </CollapsiblePanel>
</template>

<script setup>
import { reactive, ref, computed } from 'vue'
import CollapsiblePanel from '../../shared/ui/CollapsiblePanel.vue'

const props = defineProps({
  entityCounts: { type: Array, required: true },
  open: { type: Boolean, required: true },
  schema: { type: Object, required: true },
  schemas: { type: Array, default: () => [] },
  selectedSchema: { type: String, default: null },
})

defineEmits(['toggle', 'select-schema', 'delete-schema-entities', 'rebuild-schema'])

const expandedGroups = reactive({})
const showUngrouped = ref(false)

function toggleGroup(schemaId) {
  expandedGroups[schemaId] = !expandedGroups[schemaId]
}

// Classes in Neo4j schema but not in any SQLite schema group
const ungroupedClasses = computed(() => {
  const groupedNames = new Set()
  for (const s of props.schemas) {
    for (const c of s.classes || []) {
      groupedNames.add(c.class_name)
    }
  }
  return (props.schema.classes || []).filter(c => !groupedNames.has(c.name))
})
</script>

<style scoped>
.schema-group {
  margin-bottom: 4px;
}
.schema-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  cursor: pointer;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #e0e0e0;
}
.schema-group-header:hover {
  background: rgba(255, 255, 255, 0.05);
}
.schema-group-header.active {
  background: rgba(0, 120, 255, 0.15);
  border-left: 2px solid #018bff;
}
.schema-group-header.ungrouped {
  color: #888;
  font-style: italic;
}
.schema-group-toggle {
  font-size: 10px;
  width: 12px;
  flex-shrink: 0;
}
.schema-group-name {
  flex: 1;
}
.schema-group-count {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 1px 6px;
  font-size: 10px;
  color: #aaa;
}
.schema-group-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}
.schema-group-header:hover .schema-group-actions {
  opacity: 1;
}
.btn-icon-xs {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 11px;
  padding: 2px 4px;
  border-radius: 3px;
  color: #aaa;
}
.btn-icon-xs:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}
.schema-group-body {
  padding-left: 8px;
}
.indented {
  padding-left: 12px;
}
</style>
