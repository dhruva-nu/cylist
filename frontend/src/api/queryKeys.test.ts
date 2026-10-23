import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { queryKeys } from './queries'

/** Every source file under `dir`, tests and the registry itself left out. */
function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sources(path)
    return /\.tsx?$/.test(entry.name) && !entry.name.includes('.test.') ? [path] : []
  })
}

describe('queryKeys', () => {
  it('is the only place a query key is spelled out', () => {
    // A key spelled twice is two keys the day one of them is changed: the list
    // that is written on keeps its old copy, and nothing says so.
    const spelled = sources('src')
      .filter((path) => !path.endsWith(join('api', 'queries.ts')))
      .filter((path) => /queryKey:\s*\[/.test(readFileSync(path, 'utf8')))

    expect(spelled).toEqual([])
  })

  it('gives a project’s plans and reports a prefix of their dated keys', () => {
    expect(queryKeys.todayPlan('ATL', '2026-10-22').slice(0, 2)).toEqual(
      queryKeys.todayPlans('ATL'),
    )
    expect(queryKeys.dayReport('ATL', '2026-10-22', 'UTC').slice(0, 2)).toEqual(
      queryKeys.dayReports('ATL'),
    )
  })
})
