$(function(){
  // When date or service changes, fetch slots
  $('#date-input, #service-select').on('change', function(){
    const date = $('#date-input').val();
    const service = $('#service-select').val();
    if(!date || !service) return;
    $('#slot-select').html('<option>Loading...</option>');
    $.ajax({
      url: '/slots',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({date: date, service: service}),
      success: function(resp){
        const slots = resp.slots || [];
        if(slots.length===0){
          $('#slot-select').html('<option value="">No slots available</option>');
        } else {
          let html = '<option value="">-- Select Slot --</option>';
          for(const s of slots) html += `<option value="${s}">${s}</option>`;
          $('#slot-select').html(html);
        }
      },
      error: function(){
        $('#slot-select').html('<option value="">Error fetching slots</option>');
      }
    });
  });

  // Intercept booking form to show flash card and QR on confirm without redirect (AJAX)
  $('#booking-form').on('submit', function(e){
    e.preventDefault();
    const form = $(this);
    $.ajax({
      url: form.attr('action'),
      method: 'POST',
      data: form.serialize(),
      success: function(resp){
        if(resp.success){
          $('#flash-area').html(resp.html);
          // show owner's QR after slight delay
          const qr = resp.owner_qr;
          const btn = $('<div class="qr-popup"><p>Scan to pay:</p><img src="'+qr+'" style="width:200px;height:200px;"></div>');
          $('#flash-area').append(btn);
          // scroll into view
          $('html,body').animate({scrollTop: $('#flash-area').offset().top}, 600);
        } else {
          alert('Could not confirm booking');
        }
      },
      error: function(xhr){
        alert('Error: ' + (xhr.responseJSON && xhr.responseJSON.error ? xhr.responseJSON.error : 'server error'));
      }
    });
  });

  // show QR when button inside flash card clicked (delegated)
  $('#flash-area').on('click', '.show-qr', function(){
    const qr = $(this).data('qr');
    const win = window.open('', '_blank', 'width=400,height=600');
    win.document.write('<p style="font-family:Arial">Scan to Pay</p><img src="'+qr+'" style="width:320px;height:320px">');
  });

});
