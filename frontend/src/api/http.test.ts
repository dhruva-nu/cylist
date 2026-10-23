import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request } from './http'
import { queryKeys } from './queries'
import { noteIfSignedOut } from './session'

function answer(body: string, status = 200) {
  const fetched = vi.fn((_url: string, _init?: RequestInit) =>
    Promise.resolve(new Response(body, { status })),
  )
  vi.stubGlobal('fetch', fetched)
  return fetched
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('request', () => {
  it('reads a JSON body', async () => {
    answer('{"id":"a"}')

    expect(await request<{ id: string }>('/things')).toEqual({ id: 'a' })
  })

  it('answers undefined to an empty body, which is what a void call expects', async () => {
    answer('')

    expect(await request<void>('/things/1', { method: 'DELETE' })).toBeUndefined()
  })

  it('throws, rather than answering null, to a success that is not JSON', async () => {
    answer('<html>a proxy error page</html>')

    await expect(request('/things')).rejects.toMatchObject({
      status: 200,
      code: 'invalid_response',
    })
  })

  it('still turns an error envelope into an ApiError', async () => {
    answer('{"error":{"code":"conflict","message":"Taken."}}', 409)

    await expect(request('/things')).rejects.toMatchObject({ status: 409, code: 'conflict' })
  })

  it('hands the signal to fetch, so a query that is cancelled stops its request', async () => {
    const fetched = answer('{}')
    const controller = new AbortController()

    await request('/things', { signal: controller.signal })

    expect(fetched.mock.calls[0]?.[1]?.signal).toBe(controller.signal)
  })

  it('sends no signal when none is given', async () => {
    const fetched = answer('{}')

    await request('/things', { signal: undefined })

    expect(fetched.mock.calls[0]?.[1]?.signal).toBeNull()
  })
})

describe('noteIfSignedOut', () => {
  const unauthenticated = new ApiError(401, 'unauthorized', 'Sign in.')

  it('asks again who is signed in when a session ends under an open screen', () => {
    const client = new QueryClient()
    client.setQueryData(queryKeys.identity, { person: null })
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    noteIfSignedOut(client, unauthenticated)

    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.identity })
  })

  it('leaves a failed sign-in alone: there was no session to lose', () => {
    const client = new QueryClient()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    noteIfSignedOut(client, unauthenticated)

    expect(invalidate).not.toHaveBeenCalled()
  })

  it('ignores every other failure', () => {
    const client = new QueryClient()
    client.setQueryData(queryKeys.identity, { person: null })
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    noteIfSignedOut(client, new ApiError(403, 'forbidden', 'No.'))
    noteIfSignedOut(client, new Error('boom'))

    expect(invalidate).not.toHaveBeenCalled()
  })
})
