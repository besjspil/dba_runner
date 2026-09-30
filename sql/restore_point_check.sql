select name,
       to_char(time, 'DD-MON-YYYY HH24:MI:SS') rpt, 
       scn, 
       database_incarnation# INC#, 
       guarantee_flashback_database GUA, 
       storage_size/1024/1024 storage_mb, 
       preserved,
	   case
           when con_id = 0 then 'ENTIRE CDB'
           else con_id_to_con_name(con_id)
       end as container_name
from v$restore_point ;




