import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import Conversation from './Conversation'
import Queue from './Queue'
import StatusBar from './StatusBar'
import ThreadList from './ThreadList'
import { api, ApiError, type ThreadView } from './api'

const POLL_MS = 2000
const THREADS = ['threads']
const ESCALATIONS = ['escalations']
const threadKey = (id: string | null) => ['thread', id]

export default function App() {
  const client = useQueryClient()
  const [activeThread, setActiveThread] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data: health, error: healthError } = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => api.health(signal),
    refetchInterval: POLL_MS,
  })
  const { data: threads = [], error: listError } = useQuery({
    queryKey: THREADS,
    queryFn: ({ signal }) => api.threads(signal),
    refetchInterval: POLL_MS,
  })
  const { data: queue = [], error: queueError } = useQuery({
    queryKey: ESCALATIONS,
    queryFn: ({ signal }) => api.escalations(signal),
    refetchInterval: POLL_MS,
  })
  const { data: thread, error: threadError } = useQuery({
    queryKey: threadKey(activeThread),
    queryFn: activeThread === null ? skipToken : ({ signal }) => api.getThread(activeThread, signal),
    refetchInterval: ({ state }) => state.data?.awaiting_approval ? POLL_MS : false,
  })

  function refreshLists() {
    void client.invalidateQueries({ queryKey: THREADS })
    void client.invalidateQueries({ queryKey: ESCALATIONS })
  }

  async function storeThread(updated: ThreadView) {
    const queryKey = threadKey(updated.thread_id)
    await client.cancelQueries({ queryKey })
    client.setQueryData(queryKey, updated)
  }

  const send = useMutation({
    mutationFn: async ({ threadId, text }: { threadId: string | null; text: string }) => {
      const id = threadId ?? (await api.createThread()).thread_id
      return api.send(id, text)
    },
    onMutate: () => setActionError(null),
    onSuccess: async (updated, { threadId }) => {
      await storeThread(updated)
      if (threadId === null) setActiveThread((current) => current ?? updated.thread_id)
    },
    onError: (error, { threadId }) => {
      setActionError(error.message)
      if (threadId !== null) void client.invalidateQueries({ queryKey: threadKey(threadId) })
    },
    onSettled: refreshLists,
  })

  const decide = useMutation({
    mutationFn: ({ threadId, approved, note }: { threadId: string; approved: boolean; note: string }) =>
      api.decide(threadId, approved, note),
    onMutate: () => setActionError(null),
    onSuccess: storeThread,
    onError: (error, { threadId }) => {
      setActionError(
        error instanceof ApiError && error.status === 409
          ? 'This refund is no longer awaiting approval. The queue has been refreshed.'
          : error.message,
      )
      void client.invalidateQueries({ queryKey: threadKey(threadId) })
    },
    onSettled: refreshLists,
  })

  async function sendMessage(text: string) {
    try {
      await send.mutateAsync({ threadId: activeThread, text })
      return true
    } catch {
      return false
    }
  }

  const queryError = threadError ?? healthError ?? listError ?? queueError
  const error = actionError ?? queryError?.message
  const sendingHere = send.isPending && send.variables.threadId === activeThread

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden />
          <h1>Order Support Console</h1>
        </div>
        <StatusBar health={health ?? null} />
      </header>

      {error && (
        <p className="error" role="status">
          {error}
          {actionError && (
            <button className="ghost" onClick={() => setActionError(null)}>Dismiss</button>
          )}
        </p>
      )}

      <main className="layout">
        <ThreadList
          threads={threads}
          activeThread={activeThread}
          newDisabled={activeThread === null || (send.isPending && send.variables.threadId === null)}
          onOpen={setActiveThread}
          onNew={() => setActiveThread(null)}
        />
        <Conversation
          key={activeThread ?? 'new'}
          thread={thread ?? null}
          busy={sendingHere}
          disabled={send.isPending || (activeThread !== null && !thread) || !!thread?.awaiting_approval}
          onSend={sendMessage}
        />
        <Queue
          items={queue}
          activeThread={activeThread}
          busy={decide.isPending ? decide.variables.threadId : null}
          onDecide={(threadId, approved, note) => {
            if (!decide.isPending) decide.mutate({ threadId, approved, note })
          }}
          onOpen={setActiveThread}
        />
      </main>
    </div>
  )
}
