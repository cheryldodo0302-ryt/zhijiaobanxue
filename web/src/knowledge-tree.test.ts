import{describe,expect,it}from'vitest'
import{buildCompactKnowledgeTree,visibleBranchIdentity}from'./knowledge-tree'

describe('knowledge tree compaction',()=>{
  it('compresses an empty single-child chain and removes duplicate labels',()=>{
    const tree=buildCompactKnowledgeTree([
      {node_id:'chapter',node_type:'chapter',title:'第一章',markdown:'',parent_id:null,sort_order:1},
      {node_id:'section',node_type:'section',title:'1.1 数据',markdown:'',parent_id:'chapter',sort_order:2},
      {node_id:'point',node_type:'knowledge_point',title:'1.1 数据',markdown:'正文',parent_id:'section',sort_order:3},
    ])
    expect(tree).toHaveLength(1)
    expect(tree[0].node_id).toBe('point')
    expect(tree[0].label).toBe('第一章 / 1.1 数据')
    expect(tree[0]._dropFolderId).toBe('section')
    expect(visibleBranchIdentity(tree[0])).toEqual({
      nodeId:'chapter',nodeType:'chapter',parentId:null,
    })
  })

  it('keeps a branching empty chapter as a folder',()=>{
    const tree=buildCompactKnowledgeTree([
      {node_id:'chapter',node_type:'chapter',title:'第一章',markdown:'',parent_id:null,sort_order:1},
      {node_id:'section-a',node_type:'section',title:'1.1',markdown:'',parent_id:'chapter',sort_order:2},
      {node_id:'point-a',node_type:'knowledge_point',title:'定义',markdown:'A',parent_id:'section-a',sort_order:3},
      {node_id:'section-b',node_type:'section',title:'1.2',markdown:'',parent_id:'chapter',sort_order:4},
      {node_id:'point-b',node_type:'knowledge_point',title:'性质',markdown:'B',parent_id:'section-b',sort_order:5},
    ])
    expect(tree[0].node_id).toBe('chapter')
    expect(tree[0].children.map((item:any)=>item.label)).toEqual(['1.1 / 定义','1.2 / 性质'])
    expect(visibleBranchIdentity(tree[0].children[0])).toEqual({
      nodeId:'section-a',nodeType:'section',parentId:'chapter',
    })
  })
})
