import { Navigate, Route, Routes } from "react-router-dom";
import ProtectedRoute from "./ProtectedRoute.jsx";
import Layout from "../pages/Layout.jsx";
import LoginPage from "../pages/LoginPage.jsx";
import DashboardPage from "../pages/DashboardPage.jsx";
import CandidatesPage from "../pages/CandidatesPage.jsx";
import CandidateDetailPage from "../pages/CandidateDetailPage.jsx";
import JobsListPage from "../pages/JobsListPage.jsx";
import ResumeUploadPage from "../pages/ResumeUploadPage.jsx";
import JobDetailPage from "../pages/JobDetailPage.jsx";
import ApplicationsPage from "../pages/ApplicationsPage.jsx";
import InterviewsPage from "../pages/InterviewsPage.jsx";
import ReportsPage from "../pages/ReportsPage.jsx";
import ReportDetailPage from "../pages/ReportDetailPage.jsx";

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="/candidates" element={<CandidatesPage />} />
        <Route path="/candidates/:candidateId" element={<CandidateDetailPage />} />
        <Route path="/jobs" element={<JobsListPage />} />
        <Route path="/upload" element={<ResumeUploadPage />} />
        <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        <Route path="/jobs/:jobId/applications" element={<ApplicationsPage />} />
        <Route path="/interviews" element={<InterviewsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/reports/:applicationId" element={<ReportDetailPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
