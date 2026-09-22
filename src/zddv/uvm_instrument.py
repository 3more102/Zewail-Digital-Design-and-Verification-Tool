from __future__ import annotations

from pathlib import Path

from zddv.config import ProjectConfig


DEFAULT_UVM_ITEM_INSTRUMENTATION_PATH = (
    ".zddv/uvm/instrumentation/zddv_uvm_item_instrumentation.svh"
)


_SV_SOURCE = r'''`ifndef ZDDV_UVM_ITEM_INSTRUMENTATION_SVH
`define ZDDV_UVM_ITEM_INSTRUMENTATION_SVH

`include "uvm_macros.svh"

package zddv_uvm_item_instrumentation_pkg;
  import uvm_pkg::*;

  class zddv_item_record;
    string item_id;
    int sequence_id;
    string sequence_name;
    string sequencer_name;
    string item_name;
    int transaction_id;
  endclass

  class zddv_grant_record;
    time grant_time;
    int item_priority;
    bit lock_request;
  endclass

  // Portable UVM 1.2 sequencer wrapper that emits explicit ZDDV_UVM_ITEM
  // JSON evidence. It does not parse or depend on simulator-specific text.
  class zddv_instrumented_sequencer #(
    type REQ = uvm_sequence_item,
    type RSP = REQ
  ) extends uvm_sequencer #(REQ, RSP);

    zddv_grant_record m_grants[int unsigned];
    zddv_item_record m_pending[$];
    zddv_item_record m_recent[$];
    bit m_suppress_response_marker;
    int unsigned m_recent_limit = 256;

    function new(string name, uvm_component parent = null);
      super.new(name, parent);
    endfunction

    function automatic string zddv_json_escape(string value);
      string result = "";
      byte c;
      for (int i = 0; i < value.len(); i++) begin
        c = value.getc(i);
        case (c)
          8'h22: result = {result, "\\\""};
          8'h5c: result = {result, "\\\\"};
          8'h0a: result = {result, "\\n"};
          8'h0d: result = {result, "\\r"};
          8'h09: result = {result, "\\t"};
          default: result = {result, value.substr(i, i)};
        endcase
      end
      return result;
    endfunction

    function automatic zddv_item_record zddv_make_record(
      uvm_sequence_base sequence_ptr,
      uvm_sequence_item item
    );
      zddv_item_record record = new;
      record.item_id = $sformatf("%0d", item.get_inst_id());
      record.sequence_id = item.get_sequence_id();
      record.sequence_name = sequence_ptr.get_type_name();
      record.sequencer_name = get_full_name();
      record.item_name = item.get_type_name();
      record.transaction_id = item.get_transaction_id();
      return record;
    endfunction

    function automatic int zddv_find_pending(int sequence_id, int transaction_id);
      for (int i = 0; i < m_pending.size(); i++) begin
        if ((m_pending[i].sequence_id == sequence_id) &&
            (m_pending[i].transaction_id == transaction_id)) begin
          return i;
        end
      end
      return -1;
    endfunction

    function automatic int zddv_find_recent(int sequence_id, int transaction_id);
      for (int i = 0; i < m_recent.size(); i++) begin
        if ((m_recent[i].sequence_id == sequence_id) &&
            (m_recent[i].transaction_id == transaction_id)) begin
          return i;
        end
      end
      return -1;
    endfunction

    function automatic void zddv_remember_completed(zddv_item_record record);
      if (record == null) begin
        return;
      end
      m_recent.push_back(record);
      while (m_recent.size() > m_recent_limit) begin
        void'(m_recent.pop_front());
      end
    endfunction

    function automatic void zddv_emit_event(
      string event_name,
      zddv_item_record record,
      time event_time,
      string metadata_json = "{}"
    );
      string time_text;
      if (record == null) begin
        return;
      end
      time_text = $sformatf("%0t", event_time);
      $display(
        "ZDDV_UVM_ITEM {\"item_id\":\"%s\",\"event\":\"%s\",\"sequence_id\":\"%0d\",\"sequence\":\"%s\",\"sequencer\":\"%s\",\"item\":\"%s\",\"transaction_id\":%0d,\"time\":\"%s\",\"metadata\":%s}",
        zddv_json_escape(record.item_id),
        event_name,
        record.sequence_id,
        zddv_json_escape(record.sequence_name),
        zddv_json_escape(record.sequencer_name),
        zddv_json_escape(record.item_name),
        record.transaction_id,
        zddv_json_escape(time_text),
        metadata_json
      );
    endfunction

    function automatic void zddv_emit_response(
      RSP response,
      zddv_item_record record,
      string response_api
    );
      string metadata_json;
      if ((response == null) || (record == null)) begin
        return;
      end
      metadata_json = $sformatf(
        "{\"capture\":\"zddv_instrumented_sequencer\",\"response_api\":\"%s\",\"response_type\":\"%s\",\"response_inst_id\":%0d}",
        zddv_json_escape(response_api),
        zddv_json_escape(response.get_type_name()),
        response.get_inst_id()
      );
      zddv_emit_event("RESPONSE", record, $time, metadata_json);
    endfunction

    virtual task wait_for_grant(
      uvm_sequence_base sequence_ptr,
      int item_priority = -1,
      bit lock_request = 0
    );
      zddv_grant_record grant;
      int unsigned sequence_inst_id;
      super.wait_for_grant(sequence_ptr, item_priority, lock_request);
      grant = new;
      grant.grant_time = $time;
      grant.item_priority = item_priority;
      grant.lock_request = lock_request;
      sequence_inst_id = sequence_ptr.get_inst_id();
      m_grants[sequence_inst_id] = grant;
    endtask

    virtual function void send_request(
      uvm_sequence_base sequence_ptr,
      uvm_sequence_item item,
      bit rerandomize = 0
    );
      zddv_item_record record;
      zddv_grant_record grant;
      int unsigned sequence_inst_id;
      string grant_metadata;

      super.send_request(sequence_ptr, item, rerandomize);
      record = zddv_make_record(sequence_ptr, item);
      sequence_inst_id = sequence_ptr.get_inst_id();

      if (m_grants.exists(sequence_inst_id)) begin
        grant = m_grants[sequence_inst_id];
        grant_metadata = $sformatf(
          "{\"capture\":\"zddv_instrumented_sequencer\",\"item_priority\":%0d,\"lock_request\":%s}",
          grant.item_priority,
          grant.lock_request ? "true" : "false"
        );
        zddv_emit_event("GRANT", record, grant.grant_time, grant_metadata);
        m_grants.delete(sequence_inst_id);
      end

      zddv_emit_event(
        "REQUEST",
        record,
        $time,
        "{\"capture\":\"zddv_instrumented_sequencer\",\"request_api\":\"send_request\"}"
      );
      m_pending.push_back(record);
    endfunction

    virtual function void item_done(RSP response = null);
      zddv_item_record record = null;
      REQ current_item;
      int index = -1;

      current_item = get_current_item();
      if (current_item != null) begin
        index = zddv_find_pending(
          current_item.get_sequence_id(),
          current_item.get_transaction_id()
        );
      end
      if ((index < 0) && (response != null)) begin
        index = zddv_find_pending(
          response.get_sequence_id(),
          response.get_transaction_id()
        );
      end
      if ((index < 0) && (m_pending.size() > 0)) begin
        index = 0;
      end
      if (index >= 0) begin
        record = m_pending[index];
        m_pending.delete(index);
        zddv_emit_event(
          "ITEM_DONE",
          record,
          $time,
          "{\"capture\":\"zddv_instrumented_sequencer\",\"completion_api\":\"item_done\"}"
        );
        zddv_remember_completed(record);
      end

      m_suppress_response_marker = (response != null);
      super.item_done(response);
      m_suppress_response_marker = 0;

      if ((response != null) && (record != null)) begin
        zddv_emit_response(response, record, "item_done");
        index = zddv_find_recent(record.sequence_id, record.transaction_id);
        if (index >= 0) begin
          m_recent.delete(index);
        end
      end
    endfunction

    virtual task get(output REQ item);
      zddv_item_record record = null;
      int index;
      super.get(item);
      if (item != null) begin
        index = zddv_find_pending(item.get_sequence_id(), item.get_transaction_id());
        if (index >= 0) begin
          record = m_pending[index];
          m_pending.delete(index);
          zddv_emit_event(
            "ITEM_DONE",
            record,
            $time,
            "{\"capture\":\"zddv_instrumented_sequencer\",\"completion_api\":\"get\"}"
          );
          zddv_remember_completed(record);
        end
      end
    endtask

    virtual function void put_response(RSP response);
      zddv_item_record record = null;
      int index;
      super.put_response(response);
      if (m_suppress_response_marker || (response == null)) begin
        return;
      end

      index = zddv_find_recent(
        response.get_sequence_id(),
        response.get_transaction_id()
      );
      if (index >= 0) begin
        record = m_recent[index];
        m_recent.delete(index);
      end else begin
        index = zddv_find_pending(
          response.get_sequence_id(),
          response.get_transaction_id()
        );
        if (index >= 0) begin
          record = m_pending[index];
        end
      end
      zddv_emit_response(response, record, "put_response");
    endfunction

    virtual function void stop_sequences();
      super.stop_sequences();
      m_grants.delete();
      m_pending.delete();
      m_recent.delete();
      m_suppress_response_marker = 0;
    endfunction

  endclass
endpackage

`endif
'''


def render_uvm_item_instrumentation() -> str:
    return _SV_SOURCE


def write_uvm_item_instrumentation(
    project: ProjectConfig,
    *,
    output: str | Path = DEFAULT_UVM_ITEM_INSTRUMENTATION_PATH,
) -> Path:
    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_uvm_item_instrumentation(), encoding="utf-8")
    return destination
