/// Snapshot agents have profile IDs only after the gateway verifies ownership.
bool agentRequiresAttention(
  String profileId,
  List<Map<String, dynamic>> agents,
) => agents.any(
  (agent) =>
      agent['profile_id'] == profileId &&
      (agent['agent_status'] ?? agent['state']) == 'blocked',
);
