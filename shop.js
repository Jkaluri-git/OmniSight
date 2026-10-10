(function(){
  var q=new URLSearchParams(location.search).get('bugs');
  if(q!==null) sessionStorage.setItem('bugs',q);
  (sessionStorage.getItem('bugs')||'').split(',').filter(Boolean).forEach(function(b){document.body.classList.add('bug-'+b)});
  var cart=JSON.parse(localStorage.getItem('cart')||'[]');
  window.addToCart=function(n,p){cart.push({n:n,p:p});localStorage.setItem('cart',JSON.stringify(cart))};
  var el=document.getElementById('cart-lines');
  if(el){var t=0;el.innerHTML=cart.map(function(i){t+=i.p;return '<div class="row"><span>'+i.n+'</span><span>$'+i.p.toFixed(2)+'</span></div>'}).join('')||'<p class="muted">Your cart is empty.</p>';
    var tt=document.getElementById('cart-total'); if(tt) tt.textContent='$'+t.toFixed(2);}
  var f=document.getElementById('checkout-form');
  if(f) f.addEventListener('submit',function(e){e.preventDefault();localStorage.removeItem('cart');location.href='success.html'+location.search});
})();
