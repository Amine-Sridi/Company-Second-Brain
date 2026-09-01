import { useParams } from "react-router-dom";

// Placeholder -- candidate ranking, score breakdown, and selection land in a future pass.
export default function JobDetailPage() {
  const { jobId } = useParams();
  return (
    <div>
      <h1>Job {jobId}</h1>
      <p>Ranked candidates are coming in a future pass.</p>
    </div>
  );
}
