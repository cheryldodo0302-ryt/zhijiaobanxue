export function normalizeTreeTitle(value:string):string{
  return String(value||'').toLowerCase().replace(/[\s·、，。:：/\\_-]+/g,'')
}

function compactTreeItem(raw:any,allNodes:any[]):any{
  const compactRootId=String(raw.node_id)
  const compactRootType=String(raw.node_type)
  const compactParentId=raw.parent_id?String(raw.parent_id):null
  const children=(raw.children||[]).map((child:any)=>compactTreeItem(child,allNodes))
  const labels=[raw.title]
  const realIds=[raw.node_id]
  let terminal={...raw,children}
  while(!String(terminal.markdown||'').trim()&&terminal.children.length===1){
    const child=terminal.children[0]
    labels.push(...(child._compactLabels||[child.title]))
    realIds.push(...(child._compactNodeIds||[child.node_id]))
    terminal={...child}
  }
  const display:string[]=[]
  for(const label of labels){
    if(label&&!display.some(value=>normalizeTreeTitle(value)===normalizeTreeTitle(label)))display.push(label)
  }
  const hiddenFolders=realIds.slice(0,-1).reverse().map(id=>allNodes.find(x=>x.node_id===id))
  return{
    ...terminal,
    label:display.join(' / '),
    _compactLabels:display,
    _compactNodeIds:realIds,
    // A compressed row is rendered from the terminal node, but sibling drag
    // operations must move the first real node in the visible branch. Using
    // terminal.parent_id here would place a seemingly top-level row back into
    // one of its hidden folders.
    _compactRootId:compactRootId,
    _compactRootType:compactRootType,
    _compactParentId:compactParentId,
    _dropFolderId:hiddenFolders.find(x=>x?.node_type==='section')?.node_id||null,
    children:terminal.children,
  }
}

export function visibleBranchIdentity(item:any):{
  nodeId:string
  nodeType:string
  parentId:string|null
}{
  return{
    nodeId:String(item?._compactRootId||item?.node_id||''),
    nodeType:String(item?._compactRootType||item?.node_type||''),
    parentId:item?._compactParentId!==undefined
      ?(item._compactParentId?String(item._compactParentId):null)
      :(item?.parent_id?String(item.parent_id):null),
  }
}

export function buildCompactKnowledgeTree(nodes:any[]):any[]{
  const ordered=[...nodes].sort((a,b)=>Number(a.sort_order||0)-Number(b.sort_order||0)||String(a.node_id).localeCompare(String(b.node_id)))
  const map=new Map(ordered.map(item=>[item.node_id,{...item,label:item.title,children:[]}])) as Map<string,any>
  const roots:any[]=[]
  for(const item of map.values()){
    if(item.parent_id&&map.has(item.parent_id))map.get(item.parent_id).children.push(item)
    else roots.push(item)
  }
  return roots.map(item=>compactTreeItem(item,nodes))
}
