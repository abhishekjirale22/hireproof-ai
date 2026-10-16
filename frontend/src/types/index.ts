export type Recruiter = { id: number; name: string; email: string }
export type Candidate = { id: number; name: string; email: string; job_role: string; consent_audio: boolean; created_at?: string }
export type Interview = { id: number; interview_code: string; candidate_id: number; candidate_name: string; candidate_email: string; job_role: string; status: string; analysis_status: string; analysis_result: Record<string, any> | null; review_status: string; created_at: string }
export type DashboardStats = { total_interviews: number; total_candidates: number; verified_analyses: number; suspicious_results: number; inconclusive_results: number; pending_reviews: number; demo_mode: boolean }
