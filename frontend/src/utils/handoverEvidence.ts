import type { AIAsset, ExecutionAction, HandoverItem } from "../api/client";

export function actionRequiresEvidence(
  action: ExecutionAction,
  item?: HandoverItem | null,
  asset?: AIAsset | null
) {
  if (typeof action.requires_evidence === "boolean") {
    return action.requires_evidence;
  }
  if (typeof item?.requires_evidence === "boolean") {
    return item.requires_evidence;
  }
  return asset?.criticality === "high" || asset?.criticality === "critical";
}
