import { useParams } from "react-router-dom";

// Placeholder -- application tracking, invite, assessment, and accept/reject land in a
// future pass.
export default function ApplicationsPage() {
  const { jobId } = useParams();
  return (
    <div>
      <h1>Applications for job {jobId}</h1>
      <p>Application tracking is coming in a future pass.</p>
    </div>
  );
}
