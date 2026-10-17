<?php function send() { $q->update(["claimed_at" => now()]); Mail::queue($mail); }
function sendBatch() { $q->update(["claimed_at" => now(), "status" => "sending"]); Mail::queue($mail); }
