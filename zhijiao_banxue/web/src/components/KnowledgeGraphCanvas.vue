<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import * as echarts from 'echarts'

const props=withDefaults(defineProps<{
  nodes:any[]
  relations:any[]
  layout?:'force'|'circular'
  search?:string
  relationKinds?:string[]
  readonly?:boolean
}>(),{layout:'force',search:'',relationKinds:()=>[],readonly:false})
const emit=defineEmits<{selectNode:[node:any],selectRelation:[relation:any]}>()
const host=ref<HTMLElement|null>(null)
let chart:echarts.ECharts|undefined
let observer:ResizeObserver|undefined
const kindLabels:Record<string,string>={part_of:'整体—部分',prerequisite:'前置关系',progression:'后续进阶',parallel:'双向并列',related:'相关'}
const kindColors:Record<string,string>={part_of:'#378f81',prerequisite:'#b57a27',progression:'#5d8179',parallel:'#8b6e4e',related:'#899793'}
const visibleRelations=computed(()=>props.relations.filter(row=>!props.relationKinds.length||props.relationKinds.includes(row.relation_kind)))
const connectedIds=computed(()=>new Set(visibleRelations.value.flatMap(row=>[row.source_node_id,row.target_node_id])))

function category(node:any){return node.is_exam?2:node.is_difficult?1:node.is_key?0:3}
function render(){
  if(!host.value)return
  chart ||= echarts.init(host.value,null,{renderer:'canvas'})
  const needle=props.search.trim().toLocaleLowerCase()
  const graphNodes=props.nodes.filter(node=>!needle||String(node.title).toLocaleLowerCase().includes(needle)||connectedIds.value.has(node.graph_node_id)).map(node=>({
    id:node.graph_node_id,name:node.title,value:node,category:category(node),
    symbolSize:node.is_exam?48:node.is_difficult?42:node.is_key?38:30,
    itemStyle:{opacity:needle&&!String(node.title).toLocaleLowerCase().includes(needle)?.34:1},
    label:{show:Boolean(needle)||props.nodes.length<80},
  }))
  const ids=new Set(graphNodes.map(node=>node.id))
  const links=visibleRelations.value.filter(row=>ids.has(row.source_node_id)&&ids.has(row.target_node_id)).map(row=>({
    source:row.source_node_id,target:row.target_node_id,value:row,
    lineStyle:{color:kindColors[row.relation_kind]||'#899793',width:row.review_status==='approved'?1.8:1,type:row.origin==='suggested'?'dashed':'solid',opacity:.72,curveness:row.relation_kind==='parallel'?.12:0},
    symbol:row.relation_kind==='parallel'?['none','none']:['none','arrow'],symbolSize:8,
  }))
  chart.setOption({
    animationDurationUpdate:500,backgroundColor:'transparent',
    tooltip:{trigger:'item',backgroundColor:'#173e49',borderWidth:0,textStyle:{color:'#fff'},formatter:(p:any)=>p.dataType==='edge'?`${p.data.value.source_title||''} · ${kindLabels[p.data.value.relation_kind]||p.data.value.relation_label} · ${p.data.value.target_title||''}`:`<b>${p.data.name}</b><br/>${[p.data.value.is_key&&'重点',p.data.value.is_difficult&&'难点',p.data.value.is_exam&&'考点'].filter(Boolean).join(' · ')||'普通知识点'}`},
    legend:[{bottom:8,data:['重点','难点','考点','普通'],textStyle:{color:'#657773'}}],
    series:[{type:'graph',layout:props.layout==='circular'?'circular':'force',roam:true,draggable:!props.readonly,data:graphNodes,links,categories:[
      {name:'重点',itemStyle:{color:'#378f81'}},{name:'难点',itemStyle:{color:'#b57a27'}},
      {name:'考点',itemStyle:{color:'#b24b45'}},{name:'普通',itemStyle:{color:'#91aaa4'}},
    ],label:{position:'right',color:'#29443e',fontSize:11},emphasis:{focus:'adjacency',lineStyle:{width:3,opacity:1},label:{show:true,fontWeight:700}},force:{repulsion:210,edgeLength:[70,150],gravity:.08},circular:{rotateLabel:true}}],
  },true)
}
function handleClick(params:any){if(params.dataType==='node')emit('selectNode',params.data.value);else if(params.dataType==='edge')emit('selectRelation',params.data.value)}
onMounted(()=>{render();chart?.on('click',handleClick);observer=new ResizeObserver(()=>chart?.resize());if(host.value)observer.observe(host.value)})
watch(()=>[props.nodes,props.relations,props.layout,props.search,props.relationKinds],render,{deep:true})
onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose()})
</script>

<template><div ref="host" class="graph-canvas" role="img" aria-label="课程知识图谱可视化"/></template>

<style scoped>
.graph-canvas{width:100%;height:clamp(520px,68dvh,820px);border-radius:14px;background:radial-gradient(circle at 50% 45%,#fff 0,#f5faf8 70%,#eef5f2 100%)}
</style>
