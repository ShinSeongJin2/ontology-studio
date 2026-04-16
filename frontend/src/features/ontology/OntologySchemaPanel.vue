<template>
  <CollapsiblePanel title="온톨로지 스키마" :open="open" @toggle="$emit('toggle')">
    <div v-if="!schema.classes.length && !schema.relationships.length" class="empty-hint">
      스키마가 아직 없습니다. 대화로 설계를 시작하세요.
    </div>

    <div v-for="cls in schema.classes" :key="cls.name" class="schema-class">
      <div class="schema-class-header">
        <span class="schema-icon">C</span>
        <span class="schema-class-name">{{ cls.name }}</span>
      </div>
      <div v-if="cls.description" class="schema-class-desc">{{ cls.description }}</div>
      <div v-for="prop in cls.properties || []" :key="prop.name" class="schema-prop">
        <span class="prop-name">{{ prop.name }}</span>
        <span class="prop-type">{{ prop.type }}</span>
        <span v-if="prop.required" class="prop-required">*</span>
      </div>
    </div>

    <div v-if="schema.relationships.length" class="schema-rels-section">
      <div class="ctx-label">관계 유형</div>
      <div v-for="rel in schema.relationships" :key="rel.name" class="schema-rel">
        <span class="rel-from">{{ rel.from_class }}</span>
        <span class="rel-arrow">-[{{ rel.name }}]-></span>
        <span class="rel-to">{{ rel.to_class }}</span>
      </div>
    </div>

    <div v-if="entityCounts.length" class="schema-rels-section">
      <div class="ctx-label">엔티티 수</div>
      <div v-for="entityCount in entityCounts" :key="entityCount.name" class="entity-count-item">
        <span>{{ entityCount.name }}</span>
        <span class="entity-count-badge">{{ entityCount.count }}</span>
      </div>
    </div>
  </CollapsiblePanel>
</template>

<script setup>
import CollapsiblePanel from '../../shared/ui/CollapsiblePanel.vue'

defineProps({
  entityCounts: {
    type: Array,
    required: true,
  },
  open: {
    type: Boolean,
    required: true,
  },
  schema: {
    type: Object,
    required: true,
  },
})

defineEmits(['toggle'])
</script>
