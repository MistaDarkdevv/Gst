//Переключение вкладок
//>=768px
document.getElementById("defaultOpen").click();
function openTab(evt, tabName) {
    var i, tabcontent, tablinks;
    // Получить все элементы с классом = "tabcontent" и скрыть их
    tabcontent = document.getElementsByClassName("tabcontent");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    // Получить все элементы с классом = "tablinks" и удалить класс "active"
    tablinks = document.getElementsByClassName("tablinks");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    // Показать текущую вкладку и добавить класс «active» к кнопке, открывшей вкладку
    document.getElementById(tabName).style.display = "block";
    evt.currentTarget.className += " active";
}
//<768px
function changedSelect(evt,tabName) {
  var i, tabcontent, tablinks;
  if (event.currentTarget.className=="navbar-toggler tablinks") {
    openTab(evt, tabName);

  } else 
  {
    if (event.currentTarget.className=="navbar-toggler tablinks active") {
   tabcontent = document.getElementsByClassName("tabcontent");
   for (i = 0; i < tabcontent.length; i++) {
    tabcontent[i].style.display = "none";
  }
  tablinks = document.getElementsByClassName("tablinks");
  for (i = 0; i < tablinks.length; i++) {
    tablinks[i].className = tablinks[i].className.replace(" active", "");
  }
}
}
}


//Код jQuery, установливающий маску для ввода телефона
$(function(){
  $("#phone").mask("+7 (999) 999-99-99");
});


//Валидация Формы
$(function(){
  //Проверка поля ФИО
 $.validator.methods.fio_check = function( value, element ) {
  return this.optional( element ) || /^[А-Яа-яЁё\s\-]+$/.test( value );
}
$.validator.messages.fio_check = 'В поле “ФИО” допустимы только кириллица, пробел, тире';

$('#form1').validate({
 rules: {
   fio: {
     required: true,
     fio_check: true
   }
 },
 messages: {
   fio: {
     required: "Поле “ФИО” обязательно к заполнению"
   },
   phone: {
     required: "Поле “Телефон” обязательно к заполнению"
   },
   address: {
     required: "Поле “Адрес” обязательно к заполнению"
   },
   comment: {
     required: "Поле “Комментарий” обязательно к заполнению"
   }
 }
});
});



// Функция ymaps.ready() 
ymaps.ready(init);

var myMap, myPlacemark;

function init(){
myMap = new ymaps.Map("map", {
  center: [55.76, 37.64],
  zoom: 11,
  controls: []
});
//создаете точки (несколько штук)
myPlacemark = new ymaps.Placemark([55.801, 37.508],{},
      {
       iconLayout: 'default#image',
        // URL значка
        iconImageHref: '../img/metka.png',
        // Размер значка
        iconImageSize: [30, 40],
      });
myPlacemark2 = new ymaps.Placemark([55.757, 37.651],{},
      {
       iconLayout: 'default#image',
        // URL значка
        iconImageHref: '../img/metka.png',
        // Размер значка
        iconImageSize: [30, 40],
      });
myMap.geoObjects.add(myPlacemark);
myMap.geoObjects.add(myPlacemark2);

//центровка карты по всем точкам
myMap.setBounds(myMap.geoObjects.getBounds(), 
  {checkZoomRange:true}).then(function(){ if(myMap.getZoom() < 10) myMap.setZoom(11);});
   //на мобильных устройствах...
   if (/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)){
    //... отключаем перетаскивание карты
    myMap.behaviors.disable('drag');
  }
  // Добавление скролла на карту
    myMap.controls.add('zoomControl', {
      float: 'none', 
      position: { 
        bottom: 15, 
        right: 10
      }
    });
  }
