/**
 * Shared status label/tone vocabulary for an Application's `status` field.
 * Originally local to ApplicationsPage; promoted here once DashboardPage needed the
 * exact same mapping for its status-breakdown section, so "Invited"/blue means the
 * same thing in both places rather than two hand-maintained copies drifting apart.
 */
export const STATUS_LABELS = {
  selected: "Selected",
  invited: "Invited",
  interviewed: "Interviewed",
  accepted: "Accepted",
  rejected: "Rejected",
};

export const STATUS_TONE = {
  selected: "neutral",
  invited: "primary",
  interviewed: "accent",
  accepted: "success",
  rejected: "danger",
};

export const STATUS_ORDER = ["selected", "invited", "interviewed", "accepted", "rejected"];
