const cloneRemovalPreservationNotice =
    'The remote repository and saved GitHub credentials are not deleted or revoked.';
const legacyCloneRemovalNotice =
    'This older entry has no verified original folder identity. You can remove its history entry; inspect the folder before deleting it manually.';

bool cloneFolderIdentityAvailable(Map<String, dynamic> job) {
  final identity = job['destination_identity'];
  return identity is List &&
      identity.length == 2 &&
      identity[0] is int &&
      (identity[0] as int) >= 0 &&
      identity[1] is int &&
      (identity[1] as int) > 0;
}
