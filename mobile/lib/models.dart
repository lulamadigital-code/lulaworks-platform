// Lightweight view models mirroring the LulaWorks API payloads. Only the fields
// the client renders are modelled; money fields may be absent (Golden Rule).

List<Map<String, dynamic>> pageResults(dynamic body) {
  if (body is Map && body['results'] is List) {
    return (body['results'] as List).cast<Map<String, dynamic>>();
  }
  if (body is List) return body.cast<Map<String, dynamic>>();
  return const [];
}

class Project {
  Project(this.id, this.number, this.clientName, this.title, this.status,
      this.workType, this.site);

  final String id, number, clientName, title, status, workType, site;

  factory Project.fromJson(Map<String, dynamic> j) => Project(
        j['id'] as String,
        (j['number'] ?? '') as String,
        (j['client_name'] ?? '') as String,
        (j['title'] ?? '') as String,
        (j['status'] ?? '') as String,
        (j['work_type'] ?? '') as String,
        (j['site'] ?? '') as String,
      );

  bool get isReady => status == 'ready' || status == 'in_execution';
}

class Readiness {
  Readiness(this.gateStatus, this.overall, this.categories, this.blocking);

  final String gateStatus;
  final int overall;
  final Map<String, dynamic> categories;
  final List<Map<String, dynamic>> blocking;

  factory Readiness.fromJson(Map<String, dynamic> j) => Readiness(
        (j['gate_status'] ?? 'not_ready') as String,
        (j['overall'] ?? 0) as int,
        (j['categories'] as Map?)?.cast<String, dynamic>() ?? const {},
        ((j['blocking'] as List?) ?? const [])
            .cast<Map<String, dynamic>>(),
      );

  bool get open => gateStatus == 'ready' || gateStatus == 'overridden';
}

/// A Lulama consolidated draft (the AI interaction result).
class AiDraft {
  AiDraft(this.id, this.confidence, this.result);

  final String id;
  final String confidence;
  final Map<String, dynamic> result;

  factory AiDraft.fromJson(Map<String, dynamic> j) => AiDraft(
        j['id'] as String,
        (j['confidence'] ?? '0').toString(),
        (j['result'] as Map?)?.cast<String, dynamic>() ?? const {},
      );

  List<Map<String, dynamic>> get agents =>
      ((result['agents'] as List?) ?? const []).cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get proposedActions =>
      ((result['proposed_actions'] as List?) ?? const [])
          .cast<Map<String, dynamic>>();
  List<Map<String, dynamic>> get omittedAgents =>
      ((result['omitted_agents'] as List?) ?? const [])
          .cast<Map<String, dynamic>>();
  bool get requiresApproval => result['requires_human_approval'] == true;
  String? get briefing => result['executive_briefing'] as String?;
}
