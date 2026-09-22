import { describe, it, expect } from 'vitest'
import { localDateRange, percentage, taskRank } from './portrait-utils'
describe('学生画像数据呈现', () => {
  it('区分缺失与真实零值', () => {
    expect(percentage(null)).toBe('数据不足')
    expect(percentage(0)).toBe('0%')
  })
  it('显示班级分母及动态名次，补交不生成虚假排名', () => {
    expect(taskRank({rank:1,expected_count:40,top_percent:2.5,rank_dynamic:true})).toBe('第 1/40，前 2.5%（动态）')
    expect(taskRank({rank:null,expected_count:40,top_percent:null,rank_dynamic:false})).toBe('尚无按时完整提交')
  })
  it('日期范围包含结束当天并使用带时区的时间', () => {
    const range=localDateRange('2026-09-01','2026-09-30')
    expect(new Date(range.start_at).getDate()).toBe(1)
    expect(new Date(range.end_at).getMonth()).toBe(9)
    expect(range.start_at.endsWith('Z')).toBe(true)
    expect(()=>localDateRange('2026-09-30','2026-09-01')).toThrow()
  })
})
