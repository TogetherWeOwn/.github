<?php function send() { $q->update(["claimed_at" => now()]); Mail::queue($mail); }
