import { useEffect, useRef, useState } from 'react'
import { Activity, Mic, Square } from 'lucide-react'
import { api } from '../services/api'

const SAMPLE_INTERVAL_MS = 30_000

type Props = {
  interviewId: number
  enrolled: boolean
  enabled: boolean
  onRefresh: () => Promise<void>
  onToast: (message: string) => void
}

export default function ContinuousVoiceMonitor({ interviewId, enrolled, enabled, onRefresh, onToast }: Props) {
  const [consented, setConsented] = useState(false)
  const [monitoring, setMonitoring] = useState(false)
  const [checks, setChecks] = useState(0)
  const [latest, setLatest] = useState('No checks yet')
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const segmentTimer = useRef<number | null>(null)
  const mimeType = useRef<string | undefined>(undefined)
  const active = useRef(false)
  const requestPending = useRef(false)
  const checkedCount = useRef(0)

  function stopMonitoring(refresh = false) {
    active.current = false
    if (segmentTimer.current !== null) window.clearTimeout(segmentTimer.current)
    segmentTimer.current = null
    const currentRecorder = recorder.current
    recorder.current = null
    if (currentRecorder && currentRecorder.state !== 'inactive') currentRecorder.stop()
    stream.current?.getTracks().forEach(track => track.stop())
    stream.current = null
    setMonitoring(false)
    if (refresh && checkedCount.current) void onRefresh()
  }

  useEffect(() => () => {
    active.current = false
    if (segmentTimer.current !== null) window.clearTimeout(segmentTimer.current)
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
    stream.current?.getTracks().forEach(track => track.stop())
  }, [])

  function submitSample(blob: Blob, type: string) {
    if (requestPending.current) {
      setLatest('A sample was skipped because the previous provider check is still running.')
      return
    }
    requestPending.current = true
    setLatest('Sending a voice sample for provider checks…')
    const suffix = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'webm'
    const form = new FormData()
    form.append('file', new File([blob], `exam-sample-${Date.now()}.${suffix}`, { type: type || `audio/${suffix}` }))
    form.append('candidate_consent', 'true')
    form.append('confirm', 'true')
    form.append('detect_deepfake', 'true')
    void api(`/api/interviews/${interviewId}/speaker-verification`, { method: 'POST', body: form })
      .then((response: any) => {
        checkedCount.current += 1
        setChecks(checkedCount.current)
        const result = response.result?.result || {}
        const decision = result.decision || 'completed'
        const authenticity = result.deepfake?.result?.verdict
        setLatest(`Speaker check: ${decision}${authenticity ? ` · audio authenticity: ${authenticity}` : ''}`)
        void onRefresh()
      })
      .catch((error: any) => {
        setLatest('Monitoring paused after a check could not be completed.')
        stopMonitoring(true)
        onToast(`Voice monitoring paused: ${error.message}`)
      })
      .finally(() => { requestPending.current = false })
  }

  function startSegment() {
    if (!active.current || !stream.current) return
    const chunks: Blob[] = []
    let mediaRecorder: MediaRecorder
    try {
      mediaRecorder = new MediaRecorder(stream.current, mimeType.current ? { mimeType: mimeType.current } : undefined)
    } catch {
      stopMonitoring(true)
      onToast('The browser could not restart microphone sampling. Monitoring has stopped.')
      return
    }
    recorder.current = mediaRecorder
    mediaRecorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data) }
    mediaRecorder.onstop = () => {
      if (recorder.current === mediaRecorder) recorder.current = null
      if (!active.current) return
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || mimeType.current || 'audio/webm' })
      if (blob.size) submitSample(blob, blob.type)
      if (active.current) segmentTimer.current = window.setTimeout(startSegment, 0)
    }
    mediaRecorder.onerror = () => {
      stopMonitoring(true)
      onToast('The browser stopped recording. Start monitoring again if appropriate.')
    }
    mediaRecorder.start()
    segmentTimer.current = window.setTimeout(() => {
      if (mediaRecorder.state === 'recording') mediaRecorder.stop()
    }, SAMPLE_INTERVAL_MS)
  }

  async function startMonitoring() {
    if (!enabled || !enrolled || !consented) return
    const confirmed = window.confirm('Start exam voice checks? The microphone stays on while monitoring is active. About every 30 seconds, a standalone clip will be sent to DetectifAI for speaker verification and synthetic-audio detection. Each sample may consume two provider checks. Stop monitoring at any time. Results can be wrong and must not be used alone to fail an exam.')
    if (!confirmed) return
    try {
      const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
      stream.current = mediaStream
      mimeType.current = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(type => MediaRecorder.isTypeSupported(type))
      active.current = true
      requestPending.current = false
      checkedCount.current = 0
      setChecks(0)
      setLatest('Waiting for the first 30-second sample…')
      startSegment()
      setMonitoring(true)
    } catch {
      stopMonitoring()
      onToast('Microphone access was unavailable. Check browser permissions and try again.')
    }
  }

  return <div className="exam-monitor"><div className="speaker-title"><Activity size={15} /><h3>Exam voice monitoring</h3></div>
    {!enabled ? <p>Live voice checks are unavailable in demo mode. No real identity result is generated.</p> : !enrolled ? <p>Enroll the student’s consented reference voice above before starting exam checks.</p> : <>
      <p>This browser submits a standalone audio clip about every 30 seconds for speaker comparison and a separate synthetic-audio check. It is periodic sampling, not a continuous provider stream.</p>
      <label className="mini-consent"><input type="checkbox" checked={consented} disabled={monitoring} onChange={event => setConsented(event.target.checked)} /> I agree to microphone monitoring and to sending short voice samples for these checks.</label>
      <div className={`monitor-state ${monitoring ? 'active' : ''}`} aria-live="polite"><span className={monitoring ? 'recording-dot' : ''} />{monitoring ? `Microphone active · ${checks} sample${checks === 1 ? '' : 's'} checked` : 'Microphone off'}</div>
      <p className="monitor-result">{latest}</p>
      <button className={`button ${monitoring ? 'danger' : 'primary'} full speaker-button`} disabled={!consented && !monitoring} onClick={() => monitoring ? stopMonitoring(true) : void startMonitoring()}>{monitoring ? <><Square size={13} /> Stop monitoring</> : <><Mic size={14} /> Start exam checks</>}</button>
      <small className="speaker-caveat">Voice similarity and synthetic-audio results can be wrong or bypassed. They do not prove who spoke and must not automatically determine an exam outcome. If a request fails or a check is slow, monitoring pauses or skips a sample.</small>
    </>}
  </div>
}
