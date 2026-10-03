import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { auth } from "./api.js";
import { HRLayout, PublicLayout } from "./components/Layout.jsx";
import { Loading } from "./components/ui.jsx";
import Careers from "./pages/public/Careers.jsx";
import JobPage from "./pages/public/JobPage.jsx";
import Login from "./pages/hr/Login.jsx";
import Dashboard from "./pages/hr/Dashboard.jsx";
import Jobs from "./pages/hr/Jobs.jsx";
import JobDetail from "./pages/hr/JobDetail.jsx";
import Candidate from "./pages/hr/Candidate.jsx";
import Knowledge from "./pages/hr/Knowledge.jsx";
import Learning from "./pages/hr/Learning.jsx";
import Settings from "./pages/hr/Settings.jsx";
import Offer from "./pages/candidate/Offer.jsx";

// Heavy pages (Monaco editor, MediaPipe) load only when a candidate opens them.
const Assessment = lazy(() => import("./pages/candidate/Assessment.jsx"));
const Interview = lazy(() => import("./pages/candidate/Interview.jsx"));

function RequireHR({ children }) {
  return auth.token ? children : <Navigate to="/hr/login" replace />;
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<PublicLayout />}>
          <Route path="/" element={<Careers />} />
          <Route path="/jobs/:id" element={<JobPage />} />
          <Route path="/offer/:token" element={<Offer />} />
        </Route>
        <Route path="/assess/:token" element={<Assessment />} />
        <Route path="/interview/:token" element={<Interview />} />

        <Route path="/hr/login" element={<Login />} />
        <Route
          path="/hr"
          element={
            <RequireHR>
              <HRLayout />
            </RequireHR>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="jobs/:id" element={<JobDetail />} />
          <Route path="candidates/:id" element={<Candidate />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="learning" element={<Learning />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
